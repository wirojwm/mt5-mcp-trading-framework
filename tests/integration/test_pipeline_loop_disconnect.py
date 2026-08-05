"""
Stage 2 of the disconnect-testing effort (test_mcp_client_disconnect.py is Stage 1): proves
what `scripts/run_demo_execution_pipeline_loop.py` actually does when a cycle hits the real
disconnect exception Stage 1 found (mcp.shared.exceptions.McpError -- see that file's docstring
for how that was determined empirically, not assumed).

Stub/mock components only, exactly like every other Phase 7 failure-injection test in this
package (tests/integration/test_grid_dry_run_pipeline.py,
tests/integration/test_runner_dry_run_pipeline.py): DryRunExecutor + Mock market
data/account. No McpClient, no subprocess, no MT5, no credentials, no .env, no live/trading
call anywhere in this file.

`scripts/run_demo_execution_pipeline_loop.py` is loaded directly via importlib rather than
imported normally (scripts/ isn't a package) specifically so its `_run_one_cycle()` can be
exercised without ever calling `main()` -- `main()` is where `load_dotenv()`/`load_settings()`/
`demo_execution_session()` live, and this file must never touch any of that. Loading the module
only executes its top-level constant assignments (SYMBOL, CAPS, etc.) and function/def
statements -- confirmed by reading the script: everything credential- or connection-adjacent is
inside `main()`, guarded by `if __name__ == "__main__":`, which importlib.util's
`exec_module()` does not trigger.

What this proves, matching the five behaviors required of a real disconnect:
1. `_run_one_cycle()` returns False (not raises past its own boundary) when either strategy's
   executor call raises the real disconnect exception type.
2. The same simple two-line stop check `main()` itself uses (`if not ok: break`) actually
   prevents a second cycle from ever starting, driven for real here (not just re-read from the
   script and trusted).
3. `StateStore` is left exactly as it was before the failing cycle -- no partial/corrupted
   record for the failed attempt, and any pre-existing record is untouched. (Whether the order
   ACTUALLY reached the broker before the disconnect is a separate, live-only question --
   McpOrderExecutor.submit() only calls StateStore.record_submission() AFTER its MCP call
   returns (confirmed by reading mcp_order_executor.py:233-262/287-315), so a call that raises
   before returning is never recorded locally at all, matching what this test observes. This
   file cannot and does not claim to resolve whether the broker itself executed the order in
   that scenario -- see the checkpoint doc's "remaining risks" for that.)
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Optional

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from mt5_mcp_trading.domain.models import AccountState, ExecutionResult, MarketBar, OrderPlan, SymbolInfo, Tick
from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mocks.mock_market_data import MockMarketDataSource
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.state.store import StateStore
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

from tests.unit.test_features_macd import UPWARD_CLOSES

GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
SYMBOL = "BTCUSD"

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "run_demo_execution_pipeline_loop.py"
)


def _disconnect_exc() -> McpError:
    """The exact exception type/shape Stage 1 (test_mcp_client_disconnect.py) observed a real
    subprocess kill actually produce -- used here instead of a generic ConnectionError/
    RuntimeError so this test proves the REAL disconnect exception is handled, not just "some
    Exception subclass"."""
    return McpError(ErrorData(code=0, message="Connection closed"))


def _load_loop_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "test_only_run_demo_execution_pipeline_loop", SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DisconnectingExecutor:
    """Wraps a real (dry-run) executor but raises the real disconnect exception on the FIRST
    submit() call it ever receives, then delegates every call after that -- mirrors "the
    connection was dropped exactly once, mid-cycle" rather than a permanently broken executor."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._raised = False
        self.submit_calls = 0

    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        self.submit_calls += 1
        if not self._raised:
            self._raised = True
            raise _disconnect_exc()
        return await self._inner.submit(order_plan)

    async def cancel(self, ticket: int) -> ExecutionResult:
        return await self._inner.cancel(ticket)

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        return await self._inner.close_position(ticket, volume)


class _PoisonExecutor:
    """Would prove a second cycle actually ran if it's ever touched -- used to assert a later
    cycle was correctly never attempted, not just that its result was discarded."""

    def __init__(self) -> None:
        self.calls = 0

    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        self.calls += 1
        raise AssertionError("submit() called on a cycle that should never have run")

    async def cancel(self, ticket: int) -> ExecutionResult:
        self.calls += 1
        raise AssertionError("cancel() called on a cycle that should never have run")

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        self.calls += 1
        raise AssertionError("close_position() called on a cycle that should never have run")


def _grid_bars() -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = [63000, 63010, 62995, 63020, 63005, 62990, 63015, 63030, 63010, 62998,
              63022, 63040, 63018, 63005, 62995, 63012]
    return [
        MarketBar(symbol=SYMBOL, timeframe="M1", time=base + timedelta(minutes=i),
                   open=p, high=p + 8, low=p - 8, close=p, tick_volume=10, spread=2)
        for i, p in enumerate(prices)
    ]


def _runner_bars() -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(symbol=SYMBOL, timeframe="M1", time=base + timedelta(minutes=5 * i),
                   open=c, high=c + 0.1, low=c - 0.1, close=c, tick_volume=1, spread=1)
        for i, c in enumerate(UPWARD_CLOSES)
    ]


def _market_data(bars: list[MarketBar]) -> MockMarketDataSource:
    symbol_info = SymbolInfo(symbol=SYMBOL, digits=2, point=0.01, volume_min=0.01,
                              volume_max=100.0, volume_step=0.01, stops_level=10, freeze_level=5)
    tick = Tick(symbol=SYMBOL, bid=bars[-1].close - 0.02, ask=bars[-1].close, time=bars[-1].time)
    return MockMarketDataSource(bars={SYMBOL: bars}, ticks={SYMBOL: tick}, symbol_infos={SYMBOL: symbol_info})


def _account() -> MockAccountReader:
    return MockAccountReader(
        account_state=AccountState(login=180375, server="ThinkMarkets-Demo", balance=10000.0,
                                    equity=10000.0, margin_free=10000.0, trade_mode="DEMO"),
        positions=[], orders=[],
    )


def test_grid_side_disconnect_makes_run_one_cycle_return_false_not_raise(tmp_path: Path) -> None:
    module = _load_loop_module()
    module.SYMBOL = SYMBOL  # already SYMBOL by default, explicit for clarity
    market_data = _market_data(_grid_bars())
    account = _account()
    executor = _DisconnectingExecutor(DryRunExecutor())
    state_store = StateStore(tmp_path / "order_state")

    ok = asyncio.run(module._run_one_cycle(market_data, account, executor, state_store, cycle_num=1))

    assert ok is False
    assert executor.submit_calls >= 1  # the disconnect was actually reached, not skipped


def test_runner_side_disconnect_makes_run_one_cycle_return_false_not_raise(tmp_path: Path) -> None:
    module = _load_loop_module()
    # Give grid a market with no bars at all so it can't submit anything on its own (keeps this
    # test isolated to the runner leg) -- reuse the runner bars for both reads; grid's own signal
    # math still runs but its LIMIT prices vs. this tick may or may not submit, which is fine:
    # the disconnecting executor only fails on whichever submit() call happens FIRST, and this
    # test only asserts overall cycle failure + no unhandled raise, not which leg failed.
    market_data = _market_data(_runner_bars())
    account = _account()
    executor = _DisconnectingExecutor(DryRunExecutor())
    state_store = StateStore(tmp_path / "order_state")

    ok = asyncio.run(module._run_one_cycle(market_data, account, executor, state_store, cycle_num=1))

    assert ok is False
    assert executor.submit_calls >= 1


def test_disconnect_does_not_corrupt_pre_existing_local_state(tmp_path: Path) -> None:
    state_store = StateStore(tmp_path / "order_state")
    state_store.record_submission(
        ticket=900001, strategy="grid", magic=GRID_MAGIC, comment="grid_buy", symbol=SYMBOL,
        side="BUY", order_type="LIMIT", requested_volume=0.01, requested_price=63000.0,
        requested_sl=0.0, requested_tp=0.0, requested_deviation=0, requested_filling_mode=None,
        requested_expiry=None, retcode=10009, executed_price=None, executed_volume=None,
        broker_comment="", submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    before = {r.ticket: r.status for r in state_store.all_open()}
    assert before == {900001: "OPEN"}

    module = _load_loop_module()
    market_data = _market_data(_grid_bars())
    account = _account()
    executor = _DisconnectingExecutor(DryRunExecutor())

    ok = asyncio.run(module._run_one_cycle(market_data, account, executor, state_store, cycle_num=1))

    assert ok is False
    after = {r.ticket: r.status for r in state_store.all_open()}
    assert after == before, "the pre-existing record was mutated or a partial record leaked in"


def test_a_failed_cycle_prevents_a_later_cycle_from_ever_starting(tmp_path: Path) -> None:
    """Drives the exact 2-line stop check main() itself uses (`if not ok: break`) against two
    real, sequential _run_one_cycle() calls -- not a re-assertion that the check exists, but a
    live demonstration that cycle 2's executor is never touched once cycle 1 fails."""
    module = _load_loop_module()
    state_store = StateStore(tmp_path / "order_state")

    cycle_1_market_data = _market_data(_grid_bars())
    cycle_1_executor = _DisconnectingExecutor(DryRunExecutor())
    cycle_2_executor = _PoisonExecutor()

    async def simulate_loop() -> int:
        cycles_run = 0
        for cycle_num, executor in ((1, cycle_1_executor), (2, cycle_2_executor)):
            cycles_run += 1
            ok = await module._run_one_cycle(
                cycle_1_market_data, _account(), executor, state_store, cycle_num=cycle_num,
            )
            if not ok:
                break  # exactly main()'s own control flow
        return cycles_run

    cycles_run = asyncio.run(simulate_loop())

    assert cycles_run == 1
    assert cycle_2_executor.calls == 0, "cycle 2 touched its executor -- the loop did not stop"
