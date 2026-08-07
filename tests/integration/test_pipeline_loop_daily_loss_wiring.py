"""
Phase 9 Step 5 (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md): proves the REAL live-script wiring --
scripts/run_demo_execution_pipeline_loop.py's `_compute_daily_loss_decision()`/
`_daily_loss_decision_for_cycle()` and both real `should_stop()` call sites -- not just the pure
`monitoring/live_performance.compute_daily_loss_decision()` function in isolation
(tests/unit/test_monitoring_live_performance.py already covers that).

A stub McpClient stands in for the real MCP connection (same shape as
tests/unit/test_mt5_adapter_mcp_deal_history.py's _StubMcpClient, re-implementing
ToolRegistry.authorize_call() so a future change calling an unclassified/TRADING tool here would
raise, same as it would against a real McpClient) -- no live/demo call, no subprocess, no
credentials anywhere in this file. Reuses test_pipeline_loop_disconnect.py's
`_load_loop_module()`/`_market_data()`/`_account()`/`_runner_bars()`/`_PoisonExecutor` harness.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import ToolRegistry
from mt5_mcp_trading.risk.daily_loss_guard import DailyLossLimitConfig
from mt5_mcp_trading.state.store import StateStore

from tests.integration.test_pipeline_loop_disconnect import (
    _PoisonExecutor,
    _account,
    _load_loop_module,
    _market_data,
    _runner_bars,
)


class _StubMcpClient:
    def __init__(
        self,
        responses: dict[str, str],
        registry: Optional[ToolRegistry] = None,
        fail: frozenset[str] = frozenset(),
        fail_from_call: Optional[int] = None,  # 1-indexed: get_deals fails from this call onward
    ) -> None:
        self._responses = responses
        self.registry = registry if registry is not None else build_metatrader_tool_registry()
        self._fail = fail
        self._fail_from_call = fail_from_call
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> str:
        self.registry.authorize_call(name)  # mirrors McpClient.call_tool's enforcement
        self.calls.append((name, arguments or {}))
        call_num = len(self.calls)
        should_fail = name in self._fail or (
            self._fail_from_call is not None and name == "get_deals" and call_num >= self._fail_from_call
        )
        if should_fail:
            raise ConnectionError(f"stub: {name} failed")
        return self._responses[name]


_DEALS_CSV_HEADER = (
    "time,ticket,order,type,entry,magic,position_id,volume,price,commission,swap,profit,fee,"
    "symbol,comment\n"
)


def _deal_row(position_id: int, profit: float, when: datetime) -> str:
    ts = when.strftime("%Y-%m-%d %H:%M:%S")  # tz-naive on the wire, matching the real quirk
    return (
        f"{ts},{position_id + 1000},{position_id},1,1,0,{position_id},0.01,63500.0,-0.06,0.0,"
        f"{profit},0.0,BTCUSD,\n"
    )


def _submit_closed_record(state_store: StateStore, ticket: int) -> None:
    now = datetime.now(timezone.utc)
    state_store.record_submission(
        ticket=ticket, strategy="grid", magic=71101, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="LIMIT", requested_volume=0.01, requested_price=63000.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode="FOK", requested_expiry=None, retcode=10009,
        executed_price=63000.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=now,
    )
    state_store.record_closed(ticket, reason="tp hit", closed_at=now)


# ---------- _compute_daily_loss_decision ----------

def test_off_by_default_makes_no_real_call(tmp_path: Path) -> None:
    module = _load_loop_module()
    client = _StubMcpClient({})  # no responses configured -- a real call would KeyError
    state_store = StateStore(tmp_path / "order_state")
    config = DailyLossLimitConfig(max_daily_loss=None, reset_hour_utc=0)

    decision = asyncio.run(module._compute_daily_loss_decision(client, state_store, config))

    assert decision.approved is True
    assert client.calls == []  # short-circuited -- no get_deals call at all


def test_configured_and_within_limit_approves(tmp_path: Path) -> None:
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)
    now = datetime.now(timezone.utc)
    csv = _DEALS_CSV_HEADER + _deal_row(position_id=1, profit=-50.0, when=now)
    client = _StubMcpClient({"get_deals": csv})
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    decision = asyncio.run(module._compute_daily_loss_decision(client, state_store, config))

    assert decision.approved is True
    assert client.calls[0][0] == "get_deals"


def test_configured_and_breached_rejects(tmp_path: Path) -> None:
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)
    now = datetime.now(timezone.utc)
    csv = _DEALS_CSV_HEADER + _deal_row(position_id=1, profit=-600.0, when=now)
    client = _StubMcpClient({"get_deals": csv})
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    decision = asyncio.run(module._compute_daily_loss_decision(client, state_store, config))

    assert decision.approved is False
    assert decision.blocking_guard == "risk.daily_loss_limit"


def test_untracked_ticket_deals_are_never_matched(tmp_path: Path) -> None:
    # No closed record at all for position_id=1 -- StateStore.all_closed() yields no trusted
    # ids, so this real loss must NOT count (never attribute via deal.magic/position_id alone).
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")
    now = datetime.now(timezone.utc)
    csv = _DEALS_CSV_HEADER + _deal_row(position_id=1, profit=-999999.0, when=now)
    client = _StubMcpClient({"get_deals": csv})
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    decision = asyncio.run(module._compute_daily_loss_decision(client, state_store, config))

    assert decision.approved is True


def test_fetches_with_a_wide_margin_on_both_ends(tmp_path: Path) -> None:
    # Root-cause regression (2026-08-07 live smoke test, docs/PHASE9_FORWARD_TEST_CHECKPOINT.md):
    # the ORIGINAL version of this call passed from_date=boundary only (to_date defaulted deep
    # inside the vendored client), and Deal.time's +3h UTC mislabel made that narrow window miss
    # every real deal across a full 12-cycle live run. Proves the real call site now asks for a
    # full-day margin on both ends of the true reset window, not exactly the boundary.
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)
    client = _StubMcpClient({"get_deals": _DEALS_CSV_HEADER})
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    asyncio.run(module._compute_daily_loss_decision(client, state_store, config))

    assert len(client.calls) == 1
    name, args = client.calls[0]
    assert name == "get_deals"
    from_date = datetime.strptime(args["from_date"], "%Y-%m-%d")
    to_date = datetime.strptime(args["to_date"], "%Y-%m-%d")
    assert (to_date - from_date).days >= 2  # at least a full day of margin on each end


def test_raises_when_get_deals_fails(tmp_path: Path) -> None:
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)
    client = _StubMcpClient({}, fail=frozenset({"get_deals"}))
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    with pytest.raises(ConnectionError):
        asyncio.run(module._compute_daily_loss_decision(client, state_store, config))


# ---------- _daily_loss_decision_for_cycle (never raises -- fails closed) ----------

def test_fails_closed_when_computation_raises(tmp_path: Path) -> None:
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)
    client = _StubMcpClient({}, fail=frozenset({"get_deals"}))
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    decision = asyncio.run(module._daily_loss_decision_for_cycle(client, state_store, config))

    assert decision.approved is False
    assert decision.blocking_guard == "daily_loss_computation_error"


def test_does_not_fail_closed_when_computation_succeeds(tmp_path: Path) -> None:
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({})  # max_daily_loss=None short-circuits, no call needed
    config = DailyLossLimitConfig(max_daily_loss=None, reset_hour_utc=0)

    decision = asyncio.run(module._daily_loss_decision_for_cycle(client, state_store, config))

    assert decision.approved is True


# ---------- end-to-end: a real breach actually stops a real multi-cycle loop ----------

async def _drive_real_wiring(module, market_data, account, client, executors, state_store, limits, config):
    """Mirrors main()'s real while-loop shape exactly (same order of operations as the real
    script): the daily-loss decision is computed via the module's own real
    _daily_loss_decision_for_cycle() each iteration -- not a hand-fed RiskDecision list -- then
    passed into the module's own real should_stop()."""
    cycle_num = 0
    stop_reason = None
    while cycle_num < len(executors):
        decision = await module._daily_loss_decision_for_cycle(client, state_store, config)
        stop_reason = module.should_stop(cycle_num=cycle_num, elapsed_seconds=0.0,
                                          stop_file_exists=False, limits=limits,
                                          daily_loss_decision=decision)
        if stop_reason is not None:
            break
        executor = executors[cycle_num]
        cycle_num += 1
        ok = await module._run_one_cycle(market_data, account, executor, state_store, cycle_num=cycle_num)
        if not ok:
            stop_reason = "cycle error"
            break
    return cycle_num, stop_reason


def _limits(max_cycles: int = 100):
    module = _load_loop_module()
    return module.LoopLimits(max_cycles=max_cycles, max_runtime_seconds=999_999.0,
                              cycle_interval_seconds=300.0, poll_interval_seconds=5.0)


def test_a_real_breach_stops_a_real_multi_cycle_loop_before_a_further_cycle(tmp_path: Path) -> None:
    module = _load_loop_module()
    market_data = _market_data(_runner_bars())
    poison = _PoisonExecutor()
    executors = [DryRunExecutor(), DryRunExecutor(), poison]
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)

    now = datetime.now(timezone.utc)
    csv = _DEALS_CSV_HEADER + _deal_row(position_id=1, profit=-600.0, when=now)
    client = _StubMcpClient({"get_deals": csv})
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    cycles_run, stop_reason = asyncio.run(_drive_real_wiring(
        module, market_data, _account(), client, executors, state_store, _limits(), config,
    ))

    assert cycles_run == 0  # the breach is already real before cycle 1 ever starts
    assert stop_reason is not None
    assert "daily loss limit breached" in stop_reason
    assert poison.calls == 0


def test_no_breach_lets_a_real_multi_cycle_loop_run_to_completion(tmp_path: Path) -> None:
    module = _load_loop_module()
    market_data = _market_data(_runner_bars())
    executors = [DryRunExecutor(), DryRunExecutor(), DryRunExecutor()]
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)

    now = datetime.now(timezone.utc)
    csv = _DEALS_CSV_HEADER + _deal_row(position_id=1, profit=-50.0, when=now)
    client = _StubMcpClient({"get_deals": csv})
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    cycles_run, stop_reason = asyncio.run(_drive_real_wiring(
        module, market_data, _account(), client, executors, state_store, _limits(max_cycles=3), config,
    ))

    assert cycles_run == 3
    assert stop_reason is None


def test_a_computation_failure_stops_a_real_multi_cycle_loop_too(tmp_path: Path) -> None:
    # get_deals succeeds (no breach) for cycle 1's check, then starts failing from the second
    # call onward -- proving a MID-RUN computation failure fails CLOSED, exactly like a real
    # breach would, not just a failure on the very first check.
    module = _load_loop_module()
    market_data = _market_data(_runner_bars())
    poison = _PoisonExecutor()
    executors = [DryRunExecutor(), poison]
    state_store = StateStore(tmp_path / "order_state")
    _submit_closed_record(state_store, ticket=1)

    now = datetime.now(timezone.utc)
    csv = _DEALS_CSV_HEADER + _deal_row(position_id=1, profit=-50.0, when=now)
    client = _StubMcpClient({"get_deals": csv}, fail_from_call=2)
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)

    cycles_run, stop_reason = asyncio.run(_drive_real_wiring(
        module, market_data, _account(), client, executors, state_store, _limits(), config,
    ))

    assert cycles_run == 1  # cycle 1 ran for real against a genuinely approved decision
    assert stop_reason is not None
    assert "daily loss limit breached" in stop_reason  # should_stop()'s generic phrasing
    assert "failing closed" in stop_reason
    assert poison.calls == 0
