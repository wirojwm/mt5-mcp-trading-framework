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

from mt5_mcp_trading.domain.models import AccountState, MarketBar, OrderState, SymbolInfo, Tick
from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mocks.mock_market_data import MockMarketDataSource
from mt5_mcp_trading.pipeline.grid_cycle import run_grid_cycle
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
    assert executor.submitted == []
