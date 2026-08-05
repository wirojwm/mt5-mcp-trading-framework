#!/usr/bin/env python3
"""
Phase 8 Step 4's real-data run (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): stress-tests cost
sensitivity by re-running the backtest engine (backtest/engine.py) against the real cached
BTCUSD M1 history at several spread multipliers (1x, 2x, 5x observed) and reporting how each
strategy's Step-1 edge metrics (expectancy, max drawdown, both in R-multiples) move as costs
rise -- answering whether the already-negative expectancy found in Step 3 is dominated by cost
assumptions (would erode sharply here) or by something else (would barely move).

Makes exactly ONE real MCP call across the whole run: get_symbol_info(BTCUSD), read-only, once
-- all three stress levels then replay entirely offline against the same cached data and the
same fetched SymbolInfo, no further live calls regardless of how many multipliers are tested.
No order, no reference to a real OrderExecutor anywhere -- same pattern as
scripts/run_demo_execution_backtest_btcusd_m1.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.engine import run_backtest
from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars
from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.domain.models import SymbolInfo
from mt5_mcp_trading.execution.composition import demo_execution_session
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
CYCLE_INTERVAL_BARS = 5  # matches the real bounded loop's 300s cadence against M1 bars
SPREAD_MULTIPLIERS = (1.0, 2.0, 5.0)

_logger = get_logger("mt5_mcp_trading.scripts.backtest_stress_test")


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


def _report(name: str, trades: list[ClosedTrade]) -> str:
    if not trades:
        return f"{name:>8}: no closed trades"
    wins = sum(1 for t in trades if t.r_multiple > 0)
    return (
        f"{name:>8}: {len(trades):>6} trades, win rate {100 * wins / len(trades):5.1f}%, "
        f"expectancy {expectancy_r(trades):+.3f} R, max drawdown {max_drawdown_r(trades):7.3f} R, "
        f"min sample met: {has_minimum_sample(trades)}"
    )


async def main() -> None:
    _logger.info("Fetching real SymbolInfo for %s (one read-only live call, reused across all "
                 "stress levels) ...", SYMBOL)
    symbol_info = await _fetch_symbol_info()
    _logger.info("SymbolInfo: %s", symbol_info)

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path} -- run the cache-seed script first")
    _logger.info("Loaded %d cached bars: %s -> %s", len(bars), bars[0].time, bars[-1].time)

    print(f"\n=== Cost/stress sensitivity: {SYMBOL} {TIMEFRAME}, {len(bars)} bars, "
          f"{len(SPREAD_MULTIPLIERS)} spread levels ===")
    for multiplier in SPREAD_MULTIPLIERS:
        _logger.info("Running at spread_multiplier=%sx ...", multiplier)
        ledger = await run_backtest(
            bars=bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=GridStrategyConfig(),
            runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS, spread_multiplier=multiplier,
        )
        grid_trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        runner_trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
        print(f"\n-- spread_multiplier={multiplier}x --")
        print(_report("grid", grid_trades))
        print(_report("runner", runner_trades))
    print("\n=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
