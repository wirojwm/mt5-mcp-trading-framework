"""
Proves run_runner_cycle drives market data through the full pipeline into DryRunExecutor,
using Phase 2's mocks -- the runner-side counterpart of test_grid_dry_run_pipeline.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from mt5_mcp_trading.domain.models import AccountState, MarketBar, PositionState, SymbolInfo, Tick
from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mocks.mock_market_data import MockMarketDataSource
from mt5_mcp_trading.pipeline.runner_cycle import run_runner_cycle
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.state.store import StateStore
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

from tests.unit.test_features_macd import DOWNWARD_CLOSES, UPWARD_CLOSES

MAGIC = 72101
SYMBOL = "BTCUSD"


def _bars(closes: list[float]) -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(symbol=SYMBOL, timeframe="M5", time=base + timedelta(minutes=5 * i),
                   open=c, high=c + 0.1, low=c - 0.1, close=c, tick_volume=1, spread=1)
        for i, c in enumerate(closes)
    ]


def _market_data(closes: list[float]) -> MockMarketDataSource:
    bars = _bars(closes)
    symbol_info = SymbolInfo(symbol=SYMBOL, digits=2, point=0.01, volume_min=0.01,
                              volume_max=100.0, volume_step=0.01, stops_level=10, freeze_level=5)
    tick = Tick(symbol=SYMBOL, bid=bars[-1].close - 0.02, ask=bars[-1].close, time=bars[-1].time)
    return MockMarketDataSource(
        bars={SYMBOL: bars}, ticks={SYMBOL: tick}, symbol_infos={SYMBOL: symbol_info},
    )


def _account() -> MockAccountReader:
    return MockAccountReader(
        account_state=AccountState(login=180375, server="ThinkMarkets-Demo", balance=10000.0,
                                    equity=10000.0, margin_free=10000.0, trade_mode="DEMO"),
        positions=[], orders=[],
    )


def _run(market_data, executor, caps=None, account=None, state_store=None):
    return asyncio.run(run_runner_cycle(
        market_data=market_data, account=account or _account(), executor=executor,
        symbol=SYMBOL, timeframe="M5", bars_count=len(UPWARD_CLOSES),
        runner_config=RunnerStrategyConfig(),
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.02),
        caps=caps or ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
        magic=MAGIC, state_store=state_store,
    ))


def test_long_signal_reaches_the_dry_run_executor() -> None:
    executor = DryRunExecutor()
    result = _run(_market_data(UPWARD_CLOSES), executor)

    assert result is not None
    assert result.order_plan.side == "BUY"
    assert result.order_plan.order_type == "MARKET"
    assert executor.submitted == [result.order_plan]


def test_long_signal_produces_a_protected_market_order() -> None:
    # Regression test for the Phase-post-7 pipeline-wiring finding (docs/PIPELINE_WIRING_CHECKPOINT.md
    # Step 4): run_runner_cycle() used to always produce sl=tp=0.0, which McpOrderExecutor's real
    # MARKET-order validation refuses outright. Fails before the fix, passes after.
    executor = DryRunExecutor()
    result = _run(_market_data(UPWARD_CLOSES), executor)

    assert result is not None
    plan = result.order_plan
    assert plan.side == "BUY"
    assert plan.sl > 0 and plan.tp > 0
    assert plan.sl < plan.price < plan.tp  # same BUY ordering McpOrderExecutor enforces


def test_short_signal_produces_a_protected_market_order() -> None:
    executor = DryRunExecutor()
    result = _run(_market_data(DOWNWARD_CLOSES), executor)

    assert result is not None
    plan = result.order_plan
    assert plan.side == "SELL"
    assert plan.sl > 0 and plan.tp > 0
    assert plan.tp < plan.price < plan.sl  # same SELL ordering McpOrderExecutor enforces


def test_flat_signal_submits_nothing() -> None:
    executor = DryRunExecutor()
    result = _run(_market_data([100.0] * 40), executor)

    assert result is None
    assert executor.submitted == []


def test_tight_exposure_cap_blocks_the_submission() -> None:
    executor = DryRunExecutor()
    result = _run(_market_data(UPWARD_CLOSES), executor, caps=ExposureCaps(max_open_lots=0.005))

    assert result is None
    assert executor.submitted == []


# ---------- Phase 7: failure injection ----------
# Unlike run_grid_cycle, run_runner_cycle makes at most one submit() call -- there is no
# "partial results to preserve" scenario here, so a raise at any stage should simply propagate
# unchanged. These tests confirm that's actually true, not assumed.

class _RaisingMarketDataSource:
    def __init__(self, inner: MockMarketDataSource, raise_on: str, raise_exc: Exception) -> None:
        self._inner = inner
        self._raise_on = raise_on
        self._raise_exc = raise_exc

    async def get_bars(self, symbol: str, timeframe: str, count: int):
        if self._raise_on == "get_bars":
            raise self._raise_exc
        return await self._inner.get_bars(symbol, timeframe, count)

    async def get_tick(self, symbol: str):
        if self._raise_on == "get_tick":
            raise self._raise_exc
        return await self._inner.get_tick(symbol)

    async def get_symbol_info(self, symbol: str):
        if self._raise_on == "get_symbol_info":
            raise self._raise_exc
        return await self._inner.get_symbol_info(symbol)


class _RaisingExecutor:
    async def submit(self, order_plan):
        raise ConnectionError("stub: connection dropped mid-submit")

    async def cancel(self, ticket):
        raise NotImplementedError

    async def close_position(self, ticket, volume=None):
        raise NotImplementedError


@pytest.mark.parametrize("raise_on", ["get_bars", "get_tick", "get_symbol_info"])
def test_market_data_read_failure_propagates_raw(raise_on: str) -> None:
    market_data = _RaisingMarketDataSource(
        _market_data(UPWARD_CLOSES), raise_on=raise_on,
        raise_exc=ConnectionError(f"stub: {raise_on} failed"),
    )
    executor = DryRunExecutor()

    with pytest.raises(ConnectionError):
        _run(market_data, executor)
    assert executor.submitted == []


def test_executor_submit_failure_propagates_raw() -> None:
    with pytest.raises(ConnectionError):
        _run(_market_data(UPWARD_CLOSES), _RaisingExecutor())


# ---------- magic=0 quirk regression (docs/PIPELINE_WIRING_CHECKPOINT.md, post-Step-17) ----------

def test_state_store_recovers_exposure_visibility_despite_broker_magic_zero(
    tmp_path: Path,
) -> None:
    """MT5 always reports magic=0 on every position this project's own executor places, never
    the real intended magic (docs/mcp_tool_classification.md item 7) -- so
    account.get_positions(symbol, magic=magic)'s client-side filter silently matches nothing
    against real live data, making check_exposure_cap() blind to real standing exposure
    (confirmed root cause, docs/PIPELINE_WIRING_CHECKPOINT.md, post-Step-17). Reproduces the
    quirk (existing position carries magic=0, exactly like a real live read) and confirms
    passing state_store recovers correct exposure visibility via LocalOrderRecord.magic -- the
    intended value recorded locally at submission time, never overwritten by the broker's
    echoed-back 0."""
    market_data = _market_data(UPWARD_CLOSES)
    caps = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)
    existing_position = PositionState(ticket=900003, symbol=SYMBOL, side="BUY", volume=0.05,
                                       price_open=100.0, profit=0.0, magic=0)
    account = MockAccountReader(
        account_state=AccountState(login=180375, server="ThinkMarkets-Demo", balance=10000.0,
                                    equity=10000.0, margin_free=10000.0, trade_mode="DEMO"),
        positions=[existing_position], orders=[],
    )

    # Without state_store (unchanged default behavior): the magic=0 quirk blinds the exposure
    # check -- documents the bug rather than assuming it.
    blind_result = _run(market_data, DryRunExecutor(), caps=caps, account=account)
    assert blind_result is not None  # bug: 0.05 real exposure invisible, cap never tripped

    state_store = StateStore(tmp_path / "order_state")
    state_store.record_submission(
        ticket=900003, strategy="runner", magic=MAGIC, comment="runner", symbol=SYMBOL,
        side="BUY", order_type="MARKET", requested_volume=0.05, requested_price=100.0,
        requested_sl=0.0, requested_tp=0.0, requested_deviation=0, requested_filling_mode=None,
        requested_expiry=None, retcode=10009, executed_price=100.0, executed_volume=0.05,
        broker_comment="", submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    protected_result = _run(market_data, DryRunExecutor(), caps=caps, account=account,
                             state_store=state_store)

    assert protected_result is None  # fixed: 0.05 existing + 0.02 proposed exceeds 0.06, blocked
