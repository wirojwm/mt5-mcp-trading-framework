"""
Proves run_runner_cycle drives market data through the full pipeline into DryRunExecutor,
using Phase 2's mocks -- the runner-side counterpart of test_grid_dry_run_pipeline.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from mt5_mcp_trading.domain.models import AccountState, MarketBar, SymbolInfo, Tick
from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mocks.mock_market_data import MockMarketDataSource
from mt5_mcp_trading.pipeline.runner_cycle import run_runner_cycle
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

from tests.unit.test_features_macd import UPWARD_CLOSES

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


def _run(market_data, executor, caps=None):
    return asyncio.run(run_runner_cycle(
        market_data=market_data, account=_account(), executor=executor,
        symbol=SYMBOL, timeframe="M5", bars_count=len(UPWARD_CLOSES),
        runner_config=RunnerStrategyConfig(),
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.02),
        caps=caps or ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
        magic=MAGIC,
    ))


def test_long_signal_reaches_the_dry_run_executor() -> None:
    executor = DryRunExecutor()
    result = _run(_market_data(UPWARD_CLOSES), executor)

    assert result is not None
    assert result.order_plan.side == "BUY"
    assert result.order_plan.order_type == "MARKET"
    assert executor.submitted == [result.order_plan]


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
