#!/usr/bin/env python3
"""
Grid regime filter, Step 3 (docs/GRID_REGIME_FILTER_CHECKPOINT.md): out-of-sample validation of
Step 1's candidate (max_entry_efficiency_ratio=0.2) against the HELD-OUT test window -- never
read by Step 1's sweep or Step 2's tests. Same discipline as Phase 8 Step 6
(scripts/run_demo_execution_backtest_test_window_validation.py): run both the current default
(filter off) and the candidate back-to-back against the identical test window, report both
honestly in one table.

Unlike Step 1's sweep, this exercises the REAL Step-2-built pipeline path --
GridStrategyConfig(max_entry_efficiency_ratio=0.2) passed straight into run_backtest()'s existing
grid_config parameter, which pipeline/grid_cycle.py's run_grid_cycle() reads directly. The
engine's parallel grid_max_entry_efficiency_ratio simulation hook (built for Step 1, before the
production field existed) is deliberately NOT used here -- it remains available for any future
threshold re-sweep, but this validation step is specifically about proving the real implementation
this time, not the simulation.

Makes exactly ONE real MCP call across the whole run: get_symbol_info(BTCUSD), reused for both
configs. Everything else replays entirely offline against the local cache. No production default
(GridStrategyConfig) is changed by this script.
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
CYCLE_INTERVAL_BARS = 5
TRAIN_FRACTION = 0.8  # must match Step 1's split boundary exactly

_logger = get_logger("mt5_mcp_trading.scripts.backtest_regime_filter_test_window_validation")


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
    _logger.info("Test window (held out, first use for the regime filter): %d bars (%s -> %s)",
                 len(test_bars), test_bars[0].time, test_bars[-1].time)

    print(f"\n=== Grid regime filter Step 3 validation: {SYMBOL} {TIMEFRAME} -- TEST window "
          f"only (never used by Step 1's sweep) ===")
    print(f"test: {len(test_bars)} bars ({test_bars[0].time} -> {test_bars[-1].time})\n")

    configs = {
        "filter off (current default)": GridStrategyConfig(),
        "candidate max_er=0.2": GridStrategyConfig(max_entry_efficiency_ratio=0.2),
    }

    for label, grid_config in configs.items():
        print(f"--- {label} ---")
        _logger.info("running %s against test window ...", label)
        ledger = await run_backtest(
            bars=test_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=grid_config, runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        grid_trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        runner_trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
        print(_row("grid", grid_trades))
        print(_row("runner (reference)", runner_trades))
        print()

    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
