#!/usr/bin/env python3
"""
Phase 8 Step 7 (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): regime analysis for grid's still-
unresolved negative expectancy. Four single-parameter/shape hypotheses (cost, step-spacing,
decoupled SL, coupled fixed reward:risk ratio) have all been checked and rejected as fixes -- the
remaining open question is whether grid's negative expectancy is uniform across market
conditions, or concentrated in a specific regime (grid is architecturally a mean-reversion
strategy -- LIMIT orders seeded around an EMA center -- so the natural hypothesis is that it does
better when price is ranging/choppy and worse when price is trending strongly away from center).

Classifies each of grid's own real closed trades (from a single backtest run against the
TRAINING window, current production defaults, never touching the held-out test window) by
Kaufman's Efficiency Ratio (features/regime.py) computed over the atr_period-length window
immediately preceding that trade's own entry bar -- i.e. "what did the market look like right
before grid decided to enter this trade", not some global/unconditional average. Splits at the
MEDIAN observed ER (data-driven, not an arbitrary universal threshold) into two buckets so each
side comfortably clears the 30-trade minimum sample (Step 1) even with grid's relatively small
trade count (~119 at current defaults) -- a three-way tercile split was considered and rejected
for this reason.

Makes exactly ONE real MCP call across the whole run: get_symbol_info(BTCUSD). Everything else
replays entirely offline against the local cache. Produces a read-only report -- no production
default (GridStrategyConfig) is changed by this script.
"""

from __future__ import annotations

import asyncio
import dataclasses
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.engine import run_backtest
from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars, split_bars
from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.domain.models import MarketBar, SymbolInfo
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.features.regime import efficiency_ratio
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state_backtest_symbol_info_probe"  # unused, read-only
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
CYCLE_INTERVAL_BARS = 5
TRAIN_FRACTION = 0.8
ER_PERIOD = 14  # matches GridStrategyConfig.atr_period's own default -- no new magic constant

_logger = get_logger("mt5_mcp_trading.scripts.backtest_regime_analysis")


async def _fetch_symbol_info() -> SymbolInfo:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store  # deliberately never used -- read-only script
        market_data = McpMarketDataSource(client)
        return await market_data.get_symbol_info(SYMBOL)


def _row(label: str, trades: list[ClosedTrade]) -> str:
    if not trades:
        return f"{label:>10}: no closed trades"
    wins = sum(1 for t in trades if t.r_multiple > 0)
    return (
        f"{label:>10}: {len(trades):>6} trades, win rate {100 * wins / len(trades):5.1f}%, "
        f"expectancy {expectancy_r(trades):+.3f} R, max drawdown {max_drawdown_r(trades):7.3f} R, "
        f"min sample met: {has_minimum_sample(trades)}"
    )


async def main() -> None:
    _logger.info("Fetching real SymbolInfo for %s (one read-only live call) ...", SYMBOL)
    symbol_info = await _fetch_symbol_info()
    _logger.info("SymbolInfo: %s", symbol_info)

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path} -- run the cache-seed script first")
    train_bars, test_bars = split_bars(bars, train_fraction=TRAIN_FRACTION)
    del test_bars  # deliberately unused -- this script must never read the held-out window
    _logger.info("Train: %d bars (%s -> %s)", len(train_bars), train_bars[0].time, train_bars[-1].time)

    print(f"\n=== Step 7 regime analysis: {SYMBOL} {TIMEFRAME} -- TRAINING window only, "
          f"current production defaults ===")

    ledger = await run_backtest(
        bars=train_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
        symbol_info=symbol_info, grid_config=GridStrategyConfig(), runner_config=RunnerStrategyConfig(),
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
        caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
        grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
        cycle_interval_bars=CYCLE_INTERVAL_BARS,
    )
    grid_trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
    print(f"\n--- overall (unconditional, for reference) ---")
    print(_row("all", grid_trades))

    time_to_index: dict = {bar.time: i for i, bar in enumerate(train_bars)}

    def _regime_window(trade: ClosedTrade) -> list[MarketBar]:
        idx = time_to_index.get(trade.opened_at)
        if idx is None:
            raise RuntimeError(
                f"ticket={trade.ticket}: opened_at={trade.opened_at} has no matching bar in "
                f"train_bars -- the engine always opens/fills exactly on a bar's own timestamp, "
                f"this should never happen; treat as a real bug, not something to skip."
            )
        return train_bars[: idx + 1]

    er_by_trade = [(t, efficiency_ratio(_regime_window(t), period=ER_PERIOD)) for t in grid_trades]
    er_values = sorted(er for _, er in er_by_trade)
    median_er = statistics.median(er_values)
    print(f"\n--- entry-window efficiency ratio (period={ER_PERIOD}) across {len(er_values)} "
          f"grid trades: min={er_values[0]:.4f}, median={median_er:.4f}, max={er_values[-1]:.4f} ---")

    ranging_trades = [t for t, er in er_by_trade if er < median_er]
    trending_trades = [t for t, er in er_by_trade if er >= median_er]
    print(f"\n--- split at the median ER ({median_er:.4f}) -- below = RANGING, "
          f"at/above = TRENDING ---")
    print(_row("ranging", ranging_trades))
    print(_row("trending", trending_trades))

    print("\n=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
