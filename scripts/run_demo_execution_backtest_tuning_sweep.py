#!/usr/bin/env python3
"""
Phase 8 Step 5 (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): a small, evidence-driven parameter
sweep against the TRAINING portion only of the real cached BTCUSD M1 history --
backtest/market_data_cache.split_bars() carves off a held-out test portion this script never
touches, reserved entirely for Step 6's walk-forward/out-of-sample validation.

Targets Step 4's own findings, not a blind grid search:
- runner: sl_atr_mult (currently 1.5) was implicated as too tight relative to real execution
  costs (Step 4's stress test showed severe, monotonic cost sensitivity as spread widened).
  tp_atr_mult is kept at its current 2:1 ratio to sl_atr_mult throughout this sweep -- decoupling
  both is a separate, later exploration if this doesn't help, not assumed necessary now.
- grid: step_mult (currently 0.4) is the priority -- Step 4 showed grid's negative expectancy is
  NOT primarily cost-driven, so this targets entry spacing/timing instead of a cost-adjacent
  parameter.

Each sweep varies ONE strategy's parameter at a time while the OTHER strategy runs with its
current default config unchanged (both cycles always run every iteration, per run_backtest()'s
own design -- only the reported/discarded trades differ per sweep). Grid and runner's exposure
guards are evaluated per-magic independently (confirmed by reading both cycle functions), so this
one-factor-at-a-time isolation is valid, not an approximation.

Produces CANDIDATES only -- no production default (GridStrategyConfig/RunnerStrategyConfig) is
changed by this script. Archives the FULL sweep (every candidate's result), not just the best
one, per Step 5's own stated exit criteria -- picking a winner from this table is a separate,
explicit decision, not made here.

Makes exactly ONE real MCP call across the whole sweep: get_symbol_info(BTCUSD), reused for
every candidate. Everything else replays entirely offline against the local cache.
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
TRAIN_FRACTION = 0.8

RUNNER_SL_ATR_MULT_CANDIDATES = (1.5, 2.0, 2.5, 3.0, 4.0)  # current default is 1.5
# First pass (0.3-0.8) found 0.3 -- the low EDGE of that range -- as the best-expectancy point,
# non-monotonically (worse at 0.4/0.5/0.6 than at 0.3, better again by 0.8 but never beating 0.3).
# Widening below 0.3 to check whether that edge is a real, continuing trend or a reversal, before
# trusting 0.3 as a candidate -- same caution already applied to runner's 3.0/4.0 reversal.
GRID_STEP_MULT_CANDIDATES = (0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8)  # current default is 0.4
# step_mult's own sweep didn't validate out-of-sample (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md,
# Step 6) -- grid's negative expectancy isn't a cost problem (Step 4) or a step-spacing problem
# (Step 5/6). sl_atr_mult (grid's stop distance, independent of step_mult/tp's formula -- see
# strategy/grid.py's own docstring) is the next untested lever.
GRID_SL_ATR_MULT_CANDIDATES = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)  # current default is 2.0
# The sl_atr_mult sweep above looked like the cleanest signal of the phase but was found to be an
# R-unit measurement artifact (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): tp_price is derived
# from step_mult only, independent of sl_atr_mult, so widening sl alone just inflates the R
# denominator while the underlying entry/exit quality never changes. A genuinely coupled sweep
# must scale sl AND tp together at a FIXED ratio. GridStrategyConfig has no independent tp field
# by design (tp_price = atr * step_mult * 1.2, deliberately tied to step_mult so it can never go
# stale -- see strategy/grid.py's own docstring) -- step_mult is ALSO what sets the entry offset
# from center (buy_price/sell_price = center -+ step_price). This means there is no way, within
# the current architecture, to scale tp independently of the entry offset: any fixed-ratio sl/tp
# sweep necessarily moves the entry offset too. Not a flaw in this sweep's design -- an inherent
# property of grid's current formula, worth knowing regardless of this sweep's result.
# Ratio chosen: 2:1 reward:risk, matching RunnerStrategyConfig's own established convention
# (tp_atr_mult = 2 * sl_atr_mult) -- the healthiest ratio already validated elsewhere in this
# codebase, not an arbitrary pick. GRID_COUPLED_STEP_MULT_CANDIDATES reuses the exact step_mult
# values already swept above, for direct comparability; sl_atr_mult is DERIVED per candidate as
# (step_mult * 1.2) / GRID_COUPLED_RATIO so the ratio is identical at every point in this sweep.
GRID_COUPLED_RATIO = 2.0
GRID_COUPLED_STEP_MULT_CANDIDATES = (0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8)

_logger = get_logger("mt5_mcp_trading.scripts.backtest_tuning_sweep")


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
    _logger.info("Fetching real SymbolInfo for %s (one read-only live call, reused across the "
                 "whole sweep) ...", SYMBOL)
    symbol_info = await _fetch_symbol_info()
    _logger.info("SymbolInfo: %s", symbol_info)

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path} -- run the cache-seed script first")
    train_bars, test_bars = split_bars(bars, train_fraction=TRAIN_FRACTION)
    _logger.info("Loaded %d bars total: %s -> %s", len(bars), bars[0].time, bars[-1].time)
    _logger.info("Train: %d bars (%s -> %s); Test (held out, untouched this step): %d bars "
                 "(%s -> %s)", len(train_bars), train_bars[0].time, train_bars[-1].time,
                 len(test_bars), test_bars[0].time, test_bars[-1].time)

    print(f"\n=== Step 5 parameter sweep: {SYMBOL} {TIMEFRAME} -- TRAINING window only ===")
    print(f"train: {len(train_bars)} bars ({train_bars[0].time} -> {train_bars[-1].time})")
    print(f"test (held out, NOT used this step): {len(test_bars)} bars "
          f"({test_bars[0].time} -> {test_bars[-1].time})")

    print("\n--- runner: sl_atr_mult sweep (tp_atr_mult kept at 2x sl_atr_mult) ---")
    for sl_mult in RUNNER_SL_ATR_MULT_CANDIDATES:
        _logger.info("runner sweep: sl_atr_mult=%s ...", sl_mult)
        runner_config = RunnerStrategyConfig(sl_atr_mult=sl_mult, tp_atr_mult=sl_mult * 2.0)
        ledger = await run_backtest(
            bars=train_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=GridStrategyConfig(), runner_config=runner_config,
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
        print(_row(f"sl={sl_mult}", trades))

    print("\n--- grid: step_mult sweep ---")
    for step_mult in GRID_STEP_MULT_CANDIDATES:
        _logger.info("grid sweep: step_mult=%s ...", step_mult)
        grid_config = GridStrategyConfig(step_mult=step_mult)
        ledger = await run_backtest(
            bars=train_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=grid_config, runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        print(_row(f"step={step_mult}", trades))

    print("\n--- grid: sl_atr_mult sweep (step_mult held at its current default, 0.4) ---")
    for sl_mult in GRID_SL_ATR_MULT_CANDIDATES:
        _logger.info("grid sweep: sl_atr_mult=%s ...", sl_mult)
        grid_config = GridStrategyConfig(sl_atr_mult=sl_mult)
        ledger = await run_backtest(
            bars=train_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=grid_config, runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        print(_row(f"sl={sl_mult}", trades))

    print(f"\n--- grid: COUPLED sl/tp sweep, fixed {GRID_COUPLED_RATIO}:1 reward:risk ratio "
          f"(sl_atr_mult derived from step_mult -- see script comment for why entry offset "
          f"necessarily moves too) ---")
    for step_mult in GRID_COUPLED_STEP_MULT_CANDIDATES:
        sl_mult = (step_mult * 1.2) / GRID_COUPLED_RATIO
        _logger.info("grid coupled sweep: step_mult=%s -> sl_atr_mult=%s (ratio=%s) ...",
                     step_mult, sl_mult, GRID_COUPLED_RATIO)
        grid_config = GridStrategyConfig(step_mult=step_mult, sl_atr_mult=sl_mult)
        ledger = await run_backtest(
            bars=train_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=grid_config, runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        print(_row(f"step={step_mult}/sl={sl_mult:.3f}", trades))

    print("\n=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
