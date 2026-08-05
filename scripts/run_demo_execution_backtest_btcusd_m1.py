#!/usr/bin/env python3
"""
Phase 8 Step 3's real-data demonstration run (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): runs
the backtest engine (backtest/engine.py) against the real cached BTCUSD M1 history
(var/market_data/BTCUSD_M1.csv, seeded by scripts/run_demo_execution_historical_data_cache_seed.py)
and reports the resulting trade log plus Step 1's decided edge metrics (expectancy in
R-multiples, max drawdown in R-multiples), separately per strategy.

Makes exactly ONE real MCP call: get_symbol_info(BTCUSD), read-only -- real broker constraints
(point, digits, volume_min/max/step, stops_level, freeze_level), rather than hardcoding a guess.
Everything else in this script runs entirely offline against the local cache, no further live
calls. No order, no reference to a real OrderExecutor anywhere -- the backtest engine's own
BacktestOrderExecutor (a pure simulation, see engine.py) is the only "executor" this script uses.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.engine import run_backtest
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
BARS_COUNT = 100  # matches every real pipeline script's own BARS_COUNT
GRID_MAGIC = 71101  # matches this project's real registered grid magic
RUNNER_MAGIC = 72101  # matches this project's real registered runner magic
# Matches scripts/run_demo_execution_pipeline_loop.py's real CYCLE_INTERVAL_SECONDS=300 (5 min)
# against M1 bars -- confirmed necessary, not a guess: the first real run of this script at the
# engine's default cycle_interval_bars=1 (every bar) produced 27,234 runner trades over 50,000
# bars, an implausible rate traced to evaluating 5x more often than any real deployment ever
# has. See backtest/engine.py's run_backtest() docstring and the checkpoint doc's Step 3 entry.
CYCLE_INTERVAL_BARS = 5

_logger = get_logger("mt5_mcp_trading.scripts.backtest_btcusd_m1")


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


async def main() -> None:
    _logger.info("Fetching real SymbolInfo for %s (one read-only live call) ...", SYMBOL)
    symbol_info = await _fetch_symbol_info()
    _logger.info("SymbolInfo: %s", symbol_info)

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path} -- run the cache-seed script first")
    _logger.info("Loaded %d cached bars: %s -> %s", len(bars), bars[0].time, bars[-1].time)

    ledger = await run_backtest(
        bars=bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
        symbol_info=symbol_info, grid_config=GridStrategyConfig(),
        runner_config=RunnerStrategyConfig(),
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
        caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
        grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
        cycle_interval_bars=CYCLE_INTERVAL_BARS,
    )

    print(f"\n=== Backtest result: {SYMBOL} {TIMEFRAME}, {len(bars)} bars replayed ===")
    print(f"total closed trades: {len(ledger.closed_trades)}")
    print(f"still open at end: {len(ledger.open_positions)}, still pending: {len(ledger.pending_orders)}")
    for magic, name in ((GRID_MAGIC, "grid"), (RUNNER_MAGIC, "runner")):
        trades = [t for t in ledger.closed_trades if t.magic == magic]
        print(f"\n-- {name} (magic={magic}) --")
        print(f"closed trades: {len(trades)}")
        if trades:
            wins = sum(1 for t in trades if t.r_multiple > 0)
            print(f"win rate: {wins}/{len(trades)} ({100 * wins / len(trades):.1f}%)")
            print(f"expectancy: {expectancy_r(trades):.3f} R")
            print(f"max drawdown: {max_drawdown_r(trades):.3f} R")
            print(f"minimum sample (>=30 trades) met: {has_minimum_sample(trades)}")
        else:
            print("no closed trades -- cannot compute expectancy/drawdown")
    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
