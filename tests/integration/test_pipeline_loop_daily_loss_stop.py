"""
Phase 9 Step 3 (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md): proves the should_stop() wiring added in
loop_control.py actually halts a real multi-cycle run driven against
scripts/run_demo_execution_pipeline_loop.py's own `_run_one_cycle()`, not just that the pure
function returns the right string in isolation (tests/unit/test_pipeline_loop_control.py already
covers that). Reuses test_pipeline_loop_disconnect.py's already-proven harness
(`_load_loop_module()`, `_market_data()`, `_account()`, `_runner_bars()`, `_PoisonExecutor`) rather
than duplicating it -- same DryRunExecutor/mock-only shape, no McpClient, no subprocess, no MT5,
no credentials, no .env, no live/trading call anywhere in this file.

Deliberately does NOT modify or touch scripts/run_demo_execution_pipeline_loop.py's own two real
should_stop() call sites (main()'s per-cycle check, _wait_for_next_cycle()'s during-wait check)
-- neither currently passes daily_loss_decision, so the real script's behavior is byte-for-bit
unaffected by Step 3. The driver loop below (`_drive_loop()`) mirrors main()'s exact shape
(should_stop() checked BEFORE each cycle, cycle_num incremented only after the check passes,
executor failure still stops the loop) to prove what that future real wiring would do once Step 4
supplies a real realized_pnl_since_reset source -- it is a test-only harness, not new production
code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mt5_mcp_trading.domain.models import RiskDecision
from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.pipeline.loop_control import LoopLimits, should_stop
from mt5_mcp_trading.risk.daily_loss_guard import DailyLossLimitConfig, check_daily_loss_limit
from mt5_mcp_trading.state.store import StateStore

from tests.integration.test_pipeline_loop_disconnect import (
    _PoisonExecutor,
    _account,
    _load_loop_module,
    _market_data,
    _runner_bars,
)

_APPROVED = RiskDecision(approved=True, reasons=("within limit",))
_BREACHED = RiskDecision(approved=False, reasons=("realized loss breached the configured limit",),
                          blocking_guard="risk.daily_loss_limit")


def _limits(max_cycles: int = 100) -> LoopLimits:
    return LoopLimits(max_cycles=max_cycles, max_runtime_seconds=999_999.0,
                       cycle_interval_seconds=300.0, poll_interval_seconds=5.0)


async def _drive_loop(module, market_data, account, executors, state_store, limits, loss_decisions):
    """Mirrors run_demo_execution_pipeline_loop.py's real main() while-loop shape: should_stop()
    checked BEFORE each cycle, cycle_num incremented only after that check passes, a failed cycle
    (_run_one_cycle() returning False) still stops the loop -- identical control flow to today's
    script, with daily_loss_decision now wired in (the real script does not do this yet)."""
    cycle_num = 0
    stop_reason = None
    while cycle_num < len(executors):
        decision = loss_decisions[cycle_num] if cycle_num < len(loss_decisions) else None
        stop_reason = should_stop(cycle_num=cycle_num, elapsed_seconds=0.0, stop_file_exists=False,
                                   limits=limits, daily_loss_decision=decision)
        if stop_reason is not None:
            break
        executor = executors[cycle_num]
        cycle_num += 1
        ok = await module._run_one_cycle(market_data, account, executor, state_store, cycle_num=cycle_num)
        if not ok:
            stop_reason = "cycle error"
            break
    return cycle_num, stop_reason


def test_no_breach_all_cycles_run(tmp_path: Path) -> None:
    module = _load_loop_module()
    market_data = _market_data(_runner_bars())
    executors = [DryRunExecutor(), DryRunExecutor(), DryRunExecutor()]
    state_store = StateStore(tmp_path / "order_state")

    cycles_run, stop_reason = asyncio.run(_drive_loop(
        module, market_data, _account(), executors, state_store,
        limits=_limits(max_cycles=3), loss_decisions=[_APPROVED, _APPROVED, _APPROVED],
    ))

    assert cycles_run == 3
    assert stop_reason is None


def test_breach_mid_run_stops_before_a_further_cycle(tmp_path: Path) -> None:
    module = _load_loop_module()
    market_data = _market_data(_runner_bars())
    poison = _PoisonExecutor()
    executors = [DryRunExecutor(), DryRunExecutor(), poison]
    state_store = StateStore(tmp_path / "order_state")

    # Breach reported on the THIRD should_stop() check (before cycle 3) -- cycles 1 and 2 must
    # still run for real against DryRunExecutor first.
    cycles_run, stop_reason = asyncio.run(_drive_loop(
        module, market_data, _account(), executors, state_store,
        limits=_limits(), loss_decisions=[_APPROVED, _APPROVED, _BREACHED],
    ))

    assert cycles_run == 2
    assert stop_reason is not None
    assert "daily loss limit breached" in stop_reason
    assert poison.calls == 0, "cycle 3's executor was touched -- the breach did not stop the loop in time"


def test_breach_on_very_first_check_runs_zero_cycles(tmp_path: Path) -> None:
    module = _load_loop_module()
    market_data = _market_data(_runner_bars())
    poison = _PoisonExecutor()
    state_store = StateStore(tmp_path / "order_state")

    cycles_run, stop_reason = asyncio.run(_drive_loop(
        module, market_data, _account(), [poison], state_store,
        limits=_limits(), loss_decisions=[_BREACHED],
    ))

    assert cycles_run == 0
    assert stop_reason is not None
    assert poison.calls == 0


def test_real_check_daily_loss_limit_feeds_the_stop_end_to_end(tmp_path: Path) -> None:
    # Not hand-built RiskDecisions this time -- the real Step 2 function, fed a realistic
    # cumulative realized-P&L sequence across cycles, proving actual end-to-end interop between
    # Step 2's guard and Step 3's should_stop() wiring, not just that RiskDecision's shape is
    # compatible in principle.
    module = _load_loop_module()
    market_data = _market_data(_runner_bars())
    poison = _PoisonExecutor()
    executors = [DryRunExecutor(), DryRunExecutor(), poison]
    state_store = StateStore(tmp_path / "order_state")

    limit = DailyLossLimitConfig(max_daily_loss=500.0)
    # Simulates realized P&L observed immediately before each should_stop() check: breakeven
    # before cycle 1, a -100 loss before cycle 2 (still within the limit), a further -600 loss
    # before cycle 3 (now breaches the 500 limit).
    realized_pnl_before_each_cycle = [0.0, -100.0, -600.0]
    loss_decisions = [check_daily_loss_limit(pnl, limit) for pnl in realized_pnl_before_each_cycle]
    assert [d.approved for d in loss_decisions] == [True, True, False]  # sanity check on the fixture

    cycles_run, stop_reason = asyncio.run(_drive_loop(
        module, market_data, _account(), executors, state_store,
        limits=_limits(), loss_decisions=loss_decisions,
    ))

    assert cycles_run == 2
    assert stop_reason is not None
    assert "daily loss limit breached" in stop_reason
    assert "-600.00" in stop_reason
    assert poison.calls == 0
