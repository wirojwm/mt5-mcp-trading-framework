"""
Proves run_grid_cycle actually drives market data through the full pipeline and into
DryRunExecutor, using Phase 2's MockMarketDataSource/MockAccountReader -- i.e. the literal
"Phase 5: dry-run pipeline" deliverable, safe to run with no live connection at all.

This is one level up from tests/integration/test_grid_pipeline_end_to_end.py: that test
called each pipeline stage directly with hand-built values; this one calls run_grid_cycle()
itself and checks what actually got submitted to (and only to) the executor.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from mt5_mcp_trading.domain.models import AccountState, MarketBar, OrderState, SymbolInfo, Tick
from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mocks.mock_market_data import MockMarketDataSource
from mt5_mcp_trading.pipeline.grid_cycle import GridCycleError, run_grid_cycle
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig, compute_grid_levels

MAGIC = 71101
SYMBOL = "BTCUSD"
PRICES = [
    63000, 63010, 62995, 63020, 63005, 62990, 63015, 63030, 63010, 62998,
    63022, 63040, 63018, 63005, 62995, 63012,
]


def _bars() -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(symbol=SYMBOL, timeframe="M1", time=base + timedelta(minutes=i),
                   open=p, high=p + 8, low=p - 8, close=p, tick_volume=10, spread=2)
        for i, p in enumerate(PRICES)
    ]


def _market_data(tick_bid: float, tick_ask: float) -> MockMarketDataSource:
    bars = _bars()
    symbol_info = SymbolInfo(symbol=SYMBOL, digits=2, point=0.01, volume_min=0.01,
                              volume_max=100.0, volume_step=0.01, stops_level=10, freeze_level=5)
    tick = Tick(symbol=SYMBOL, bid=tick_bid, ask=tick_ask, time=bars[-1].time)
    return MockMarketDataSource(
        bars={SYMBOL: bars}, ticks={SYMBOL: tick}, symbol_infos={SYMBOL: symbol_info},
    )


def _account(orders: list[OrderState] | None = None) -> MockAccountReader:
    return MockAccountReader(
        account_state=AccountState(login=180375, server="ThinkMarkets-Demo", balance=10000.0,
                                    equity=10000.0, margin_free=10000.0, trade_mode="DEMO"),
        positions=[], orders=orders or [],
    )


def _run(market_data, account, executor, caps=None):
    return asyncio.run(run_grid_cycle(
        market_data=market_data, account=account, executor=executor,
        symbol=SYMBOL, timeframe="M1", bars_count=len(PRICES),
        grid_config=GridStrategyConfig(atr_period=14, center_ema_period=10, step_mult=0.4),
        money_config=MoneyConfig(lot_size_mode="atr_scale", atr_scale_base=0.01,
                                  atr_scale_ref=1.0, atr_scale_min=0.01, atr_scale_max=0.06),
        caps=caps or ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
        magic=MAGIC,
    ))


class _RaisingOnSideExecutor:
    """Wraps a real executor but raises for one specific side's submit() call -- proves a
    mid-cycle failure on one side doesn't destroy or block the other side's result. Phase 7."""

    def __init__(self, inner, raise_on_side: str, raise_exc: Exception) -> None:
        self._inner = inner
        self._raise_on_side = raise_on_side
        self._raise_exc = raise_exc
        self.submitted: list = []

    async def submit(self, order_plan):
        if order_plan.side == self._raise_on_side:
            raise self._raise_exc
        result = await self._inner.submit(order_plan)
        self.submitted.append(order_plan)
        return result

    async def cancel(self, ticket: int):
        return await self._inner.cancel(ticket)

    async def close_position(self, ticket: int, volume: Optional[float] = None):
        return await self._inner.close_position(ticket, volume)


class _RaisingMarketDataSource:
    """Wraps a real MarketDataSource but raises on one named call -- proves a read-stage
    failure (before any submission is attempted) propagates raw, unwrapped. Phase 7."""

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


class _RaisingAccountReader:
    """Wraps a real AccountReader but raises on one named call -- same purpose as
    _RaisingMarketDataSource, for the positions/orders read stage. Phase 7."""

    def __init__(self, inner: MockAccountReader, raise_on: str, raise_exc: Exception) -> None:
        self._inner = inner
        self._raise_on = raise_on
        self._raise_exc = raise_exc

    async def get_account_state(self):
        return await self._inner.get_account_state()

    async def get_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None):
        if self._raise_on == "get_positions":
            raise self._raise_exc
        return await self._inner.get_positions(symbol=symbol, magic=magic)

    async def get_orders(self, symbol: Optional[str] = None, magic: Optional[int] = None):
        if self._raise_on == "get_orders":
            raise self._raise_exc
        return await self._inner.get_orders(symbol=symbol, magic=magic)

    async def get_connection_state(self):
        return await self._inner.get_connection_state()


def test_both_sides_reach_the_dry_run_executor_and_nothing_else() -> None:
    # center is ~63010 for this fixture; keep bid/ask tight around it so both LIMIT prices
    # normalize successfully (same reasoning as test_grid_pipeline_end_to_end.py).
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    executor = DryRunExecutor()

    results = _run(market_data, _account(), executor)

    assert len(results) == 2
    assert {r.order_plan.side for r in results} == {"BUY", "SELL"}
    assert all(r.success for r in results)
    assert len(executor.submitted) == 2  # nothing else was ever called
    assert executor.cancelled == []
    assert executor.closed == []


def test_duplicate_pending_order_blocks_only_that_side_end_to_end() -> None:
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    # Figure out the proposed buy price the same way the pipeline will, to place a
    # deliberately colliding pending order at (approximately) that price.
    levels = compute_grid_levels(_bars(), point=0.01,
                                  config=GridStrategyConfig(atr_period=14, center_ema_period=10, step_mult=0.4))
    existing = [OrderState(ticket=555, symbol=SYMBOL, side="BUY", volume=0.01,
                            price=levels.buy_price, magic=MAGIC)]

    executor = DryRunExecutor()
    results = _run(market_data, _account(orders=existing), executor)

    assert len(results) == 1  # only SELL got through
    assert results[0].order_plan.side == "SELL"
    assert len(executor.submitted) == 1


def test_tight_exposure_cap_blocks_both_sides_end_to_end() -> None:
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    executor = DryRunExecutor()

    results = _run(market_data, _account(), executor, caps=ExposureCaps(max_open_lots=0.005))

    assert results == []


# ---------- Phase 7: failure injection ----------

def test_sell_side_failure_preserves_buy_sides_result() -> None:
    """The core Phase 7 fix: BUY succeeds, SELL's executor.submit() raises -- BUY's result
    must not be silently lost, and the failure must not be silently swallowed either."""
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    boom = ConnectionError("stub: connection dropped mid-cycle")
    executor = _RaisingOnSideExecutor(DryRunExecutor(), raise_on_side="SELL", raise_exc=boom)

    with pytest.raises(GridCycleError) as exc_info:
        _run(market_data, _account(), executor)

    err = exc_info.value
    assert len(err.completed_results) == 1
    assert err.completed_results[0].order_plan.side == "BUY"  # not lost
    assert len(err.errors) == 1
    assert err.errors[0] == ("SELL", boom)
    assert executor.submitted == [err.completed_results[0].order_plan]  # BUY really was submitted


def test_buy_side_failure_still_lets_sell_side_be_attempted() -> None:
    """The other direction: BUY raises first, but SELL must still be attempted regardless --
    one side's failure never blocks the other from being tried."""
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    boom = ConnectionError("stub: connection dropped mid-cycle")
    executor = _RaisingOnSideExecutor(DryRunExecutor(), raise_on_side="BUY", raise_exc=boom)

    with pytest.raises(GridCycleError) as exc_info:
        _run(market_data, _account(), executor)

    err = exc_info.value
    assert len(err.completed_results) == 1
    assert err.completed_results[0].order_plan.side == "SELL"  # still attempted and succeeded
    assert len(err.errors) == 1
    assert err.errors[0][0] == "BUY"


def test_both_sides_failing_reports_both_errors_and_zero_completed() -> None:
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    boom = ConnectionError("stub: connection dropped")

    class _AlwaysRaisingExecutor:
        async def submit(self, order_plan):
            raise boom

        async def cancel(self, ticket):
            raise NotImplementedError

        async def close_position(self, ticket, volume=None):
            raise NotImplementedError

    with pytest.raises(GridCycleError) as exc_info:
        _run(market_data, _account(), _AlwaysRaisingExecutor())

    err = exc_info.value
    assert err.completed_results == []
    assert {side for side, _ in err.errors} == {"BUY", "SELL"}


@pytest.mark.parametrize("raise_on", ["get_bars", "get_tick", "get_symbol_info"])
def test_market_data_read_failure_propagates_raw_not_wrapped(raise_on: str) -> None:
    """A failure before the per-side loop even starts has nothing to preserve -- the raw
    exception must propagate unchanged, not get wrapped in GridCycleError."""
    market_data = _RaisingMarketDataSource(
        _market_data(tick_bid=63009.0, tick_ask=63011.0), raise_on=raise_on,
        raise_exc=ConnectionError(f"stub: {raise_on} failed"),
    )
    executor = DryRunExecutor()

    with pytest.raises(ConnectionError):
        _run(market_data, _account(), executor)
    assert executor.submitted == []  # nothing was ever attempted


@pytest.mark.parametrize("raise_on", ["get_positions", "get_orders"])
def test_account_read_failure_propagates_raw_not_wrapped(raise_on: str) -> None:
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    account = _RaisingAccountReader(
        _account(), raise_on=raise_on, raise_exc=ConnectionError(f"stub: {raise_on} failed"),
    )
    executor = DryRunExecutor()

    with pytest.raises(ConnectionError):
        _run(market_data, account, executor)
    assert executor.submitted == []


# ---------- Phase 7: repeated-cycle regression ----------

def test_second_cycle_sees_first_cycles_submission_as_a_duplicate() -> None:
    """Regression test across two real, chained cycle calls (not a single call with
    pre-seeded state, unlike test_duplicate_pending_order_blocks_only_that_side_end_to_end
    above): cycle 1's BUY submission must be correctly recognized as a duplicate by cycle 2,
    proving the duplicate-order guard works across separate invocations, not just within one."""
    market_data = _market_data(tick_bid=63009.0, tick_ask=63011.0)
    executor = DryRunExecutor()

    cycle_1 = _run(market_data, _account(), executor)
    assert len(cycle_1) == 2  # both sides went through the first time

    buy_result = next(r for r in cycle_1 if r.order_plan.side == "BUY")
    # Approximates what a real account would now show: cycle 1's BUY is a live pending order.
    now_pending = [OrderState(
        ticket=1, symbol=SYMBOL, side="BUY", volume=buy_result.order_plan.volume,
        price=buy_result.order_plan.price, magic=MAGIC,
    )]

    cycle_2 = _run(market_data, _account(orders=now_pending), executor)

    assert len(cycle_2) == 1  # BUY correctly blocked as a duplicate of cycle 1's order
    assert cycle_2[0].order_plan.side == "SELL"
    assert len(executor.submitted) == 3  # cycle 1's 2 + cycle 2's 1 -- BUY never resubmitted
