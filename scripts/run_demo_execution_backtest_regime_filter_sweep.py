#!/usr/bin/env python3
"""
Grid regime filter, Step 1 (docs/GRID_REGIME_FILTER_CHECKPOINT.md): a threshold sweep for
GridStrategyConfig's proposed (not yet built) max_entry_efficiency_ratio field, against the
TRAINING portion of the real cached BTCUSD M1 history only -- split_bars() carves off the same
held-out test portion every other Phase 8 sweep has reserved for out-of-sample validation, never
touched here.

Deliberately does NOT modify GridStrategyConfig or pipeline/grid_cycle.py (the actual live
pipeline) -- backtest/engine.py's run_backtest() grew two new, opt-in, default-None parameters
(grid_max_entry_efficiency_ratio/grid_efficiency_ratio_period) specifically so this sweep can
dynamically, correctly simulate the filter's real effect (a skipped cycle genuinely frees up
exposure-cap slots for later cycles) without building the production config/pipeline change
before a threshold has been chosen and validated. See engine.py's run_backtest() docstring for
the full reasoning.

Candidates span Step 7's own observed ER distribution (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md:
min=0.0101, median=0.3847, max=0.8209) plus a "no filter" baseline for direct comparison.

Makes exactly ONE real MCP call across the whole sweep: get_symbol_info(BTCUSD), reused for every
candidate. Everything else replays entirely offline against the local cache. Produces CANDIDATES
only -- no production default anywhere is changed by this script.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path
from typing import Optional

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
TRAIN_FRACTION = 0.8
ER_PERIOD = 14  # matches GridStrategyConfig.atr_period's own default

# None = no filter (baseline, current behavior). First pass (0.2-0.7) found 0.2 -- the low EDGE
# of that range -- as the best-expectancy point, monotonically improving all the way down to it.
# Widening below 0.2 to check whether that edge is a real, continuing trend or a reversal, before
# trusting 0.2 as a candidate -- same caution already applied to grid's step_mult and runner's
# sl_atr_mult sweeps earlier in Phase 8.
THRESHOLD_CANDIDATES: tuple[Optional[float], ...] = (
    None, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7,
)

_logger = get_logger("mt5_mcp_trading.scripts.backtest_regime_filter_sweep")


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
        return f"{label:>12}: no closed trades"
    wins = sum(1 for t in trades if t.r_multiple > 0)
    return (
        f"{label:>12}: {len(trades):>6} trades, win rate {100 * wins / len(trades):5.1f}%, "
        f"expectancy {expectancy_r(trades):+.3f} R, max drawdown {max_drawdown_r(trades):7.3f} R, "
        f"min sample met: {has_minimum_sample(trades)}"
    )


async def main() -> None:
    _logger.info("Fetching real SymbolInfo for %s (one read-only live call, reused across the "
                 "whole sweep) ...", SYMBOL)
    symbol_info = await _fetch_symbol_info()
    _logger.info("SymbolInfo: %s", symbol_info)

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path} -- run the cache-seed script first")
    train_bars, test_bars = split_bars(bars, train_fraction=TRAIN_FRACTION)
    del test_bars  # deliberately unused -- this script must never read the held-out window
    _logger.info("Train: %d bars (%s -> %s)", len(train_bars), train_bars[0].time, train_bars[-1].time)

    print(f"\n=== Grid regime filter Step 1 sweep: {SYMBOL} {TIMEFRAME} -- TRAINING window "
          f"only ===")
    print(f"train: {len(train_bars)} bars ({train_bars[0].time} -> {train_bars[-1].time})\n")

    for threshold in THRESHOLD_CANDIDATES:
        _logger.info("regime filter sweep: max_entry_efficiency_ratio=%s ...", threshold)
        ledger = await run_backtest(
            bars=train_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=GridStrategyConfig(), runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
            grid_max_entry_efficiency_ratio=threshold,
            grid_efficiency_ratio_period=ER_PERIOD,
        )
        trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        label = "unfiltered" if threshold is None else f"max_er={threshold}"
        print(_row(label, trades))

    print("\n=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
