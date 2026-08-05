#!/usr/bin/env python3
"""
Phase 8 Step 6 (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): walk-forward / out-of-sample
validation. Runs the Step 5 candidate parameter set -- decided entirely from the TRAINING
window -- against the held-out TEST window for the first time in this phase. This script never
reads train_bars at all; only test_bars (the same split_bars(bars, train_fraction=0.8) boundary
Step 5 used) is ever passed into run_backtest().

Runs BOTH the current production defaults and the Step 5 candidates against the identical test
window, so the comparison is honest and self-contained in one report -- not two separate runs a
reader has to reconcile by hand.

Candidates (locked in Step 5, training-window evidence only until this script runs):
- runner: sl_atr_mult=3.0, tp_atr_mult=6.0 (current default: sl_atr_mult=1.5, tp_atr_mult=3.0)
- grid: step_mult=0.25 (current default: step_mult=0.4)

A disappointing result here is a valid, useful phase outcome per Step 6's own exit criteria in
the checkpoint doc -- not a failed phase. Whatever this script prints is reported as-is, not
cherry-picked.

Makes exactly ONE real MCP call across the whole run: get_symbol_info(BTCUSD), reused for both
configs. Everything else replays entirely offline against the local cache. No production default
(GridStrategyConfig/RunnerStrategyConfig) is changed by this script.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.engine import run_backtest
from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars, split_bars
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
TRAIN_FRACTION = 0.8  # must match Step 5's sweep script exactly -- same split boundary

_logger = get_logger("mt5_mcp_trading.scripts.backtest_test_window_validation")


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
        return f"{label:>18}: no closed trades"
    wins = sum(1 for t in trades if t.r_multiple > 0)
    return (
        f"{label:>18}: {len(trades):>6} trades, win rate {100 * wins / len(trades):5.1f}%, "
        f"expectancy {expectancy_r(trades):+.3f} R, max drawdown {max_drawdown_r(trades):7.3f} R, "
        f"min sample met: {has_minimum_sample(trades)}"
    )


async def main() -> None:
    _logger.info("Fetching real SymbolInfo for %s (one read-only live call, reused across both "
                 "configs) ...", SYMBOL)
    symbol_info = await _fetch_symbol_info()
    _logger.info("SymbolInfo: %s", symbol_info)

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path} -- run the cache-seed script first")
    train_bars, test_bars = split_bars(bars, train_fraction=TRAIN_FRACTION)
    del train_bars  # deliberately unused -- this script must never read the training window
    _logger.info("Loaded %d bars total: %s -> %s", len(bars), bars[0].time, bars[-1].time)
    _logger.info("Test window (held out, first use in this phase): %d bars (%s -> %s)",
                 len(test_bars), test_bars[0].time, test_bars[-1].time)

    print(f"\n=== Step 6 walk-forward validation: {SYMBOL} {TIMEFRAME} -- TEST window only "
          f"(never read by Step 5) ===")
    print(f"test: {len(test_bars)} bars ({test_bars[0].time} -> {test_bars[-1].time})\n")

    configs = {
        "current defaults": (GridStrategyConfig(), RunnerStrategyConfig()),
        "Step 5 candidates": (
            GridStrategyConfig(step_mult=0.25),
            RunnerStrategyConfig(sl_atr_mult=3.0, tp_atr_mult=6.0),
        ),
    }

    for label, (grid_config, runner_config) in configs.items():
        print(f"--- {label} "
              f"(grid step_mult={grid_config.step_mult}, "
              f"runner sl_atr_mult={runner_config.sl_atr_mult}/"
              f"tp_atr_mult={runner_config.tp_atr_mult}) ---")
        _logger.info("running %s against test window ...", label)
        ledger = await run_backtest(
            bars=test_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=grid_config, runner_config=runner_config,
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        grid_trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        runner_trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
        print(_row("grid", grid_trades))
        print(_row("runner", runner_trades))
        print()

    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
