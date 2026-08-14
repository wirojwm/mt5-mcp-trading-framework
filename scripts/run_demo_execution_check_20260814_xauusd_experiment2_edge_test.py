#!/usr/bin/env python3
"""
XAUUSD signal-edge research, Step 2 (approved 2026-08-14, docs/XAUUSD_SIGNAL_EDGE_CHECKPOINT.md):
does the CURRENTLY-DEPLOYED production runner strategy (unmodified `RunnerStrategyConfig`
defaults -- fast=12/slow=26/atr_period=14/sl_atr_mult=3.0/tp_atr_mult=6.0/
min_stop_distance_fraction_of_price=0.01/max_concurrent_positions=1, the exact config this
project would actually deploy) carry any genuine directional edge on XAUUSD? Directly answers
Live Pilot's unmet criterion 1 ("a dedicated research pass... for the chosen symbol specifically
-- BTCUSD's tuned parameters transferring by assumption is explicitly disqualifying") -- this is
NOT a hypothetical redesign, it is the real production config, unmodified, same as
`docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md` Experiment 2's own scope but for a new
instrument.

METHODOLOGY: Experiment 2's own edge test, reused verbatim -- real MACD-sign signal vs. a
same-cost/same-risk random-direction baseline (20 pre-registered seeds, runtime-monkeypatched via
`unittest.mock.patch` on `pipeline.runner_cycle.runner_signal`, nothing on disk touched), driven
through the real, unmodified `backtest.engine.run_backtest()` pipeline (not a harness -- unlike
Experiment 5, this step needs no fixed-hold mechanism, so the real cycle-based engine applies
as-is). `GridStrategyConfig()` production defaults also run alongside (the pipeline always
evaluates both strategies each cycle) and its trades are reported as a REFERENCE row only -- no
random baseline for grid, since grid's LIMIT-based signal isn't a single directional call the way
runner's is, and this step's scope is specifically the currently-deployed runner strategy.

WINDOWS: three chronological thirds (EARLY/MIDDLE/RECENT) of XAUUSD's own cached M1 history --
NOT recycling BTCUSD's specific calendar boundaries (this checkpoint's own explicit non-goal).
Unlike BTCUSD, XAUUSD has never been live-traded by this project, so there is no genuine "LIVE"
window to add as a fourth, independent check the way Experiment 2 had for BTCUSD -- three
chronological thirds is the same convention Experiment 4 already established for a symbol/
timeframe with no live-trading history.

ONE read-only live call this script makes: `get_symbol_info('XAUUSD')`, needed because this
project has never recorded XAUUSD's exact `digits`/`point`/`stops_level`/`freeze_level`/
`filling_modes` in a form a backtest script can consume directly (docs/LIVE_PILOT_PREPARATION_
CHECKPOINT.md's own table only carries derived percentages, not the raw SymbolInfo fields) --
same read-only, non-order-submitting pattern this session already used for Step 1's cache seed.
BACKTEST/OFFLINE ONLY beyond that call -- no MCP trading call, no Step 7, no Live Pilot, no
production default changed anywhere (every config instance here is local to this script).
"""

from __future__ import annotations

import asyncio
import dataclasses
import random
import statistics
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.engine import run_backtest
from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars
from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r, profit_factor, win_rate
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.domain.models import MarketBar, Signal, SymbolInfo
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state_xauusd_symbol_info_probe"
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "XAUUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
CYCLE_INTERVAL_BARS = 5  # matches real deployment cadence (300s / 60s per M1 bar), same as every prior script
SEEDS = list(range(20))  # pre-registered, same convention as Experiment 2

# Production defaults, UNMODIFIED -- this is the actual point of this step (criterion 1), not a
# hypothetical variant. No floor override, unlike Experiment 2's BTCUSD apples-to-apples choice.
RUNNER_CONFIG = RunnerStrategyConfig()
GRID_CONFIG = GridStrategyConfig()


def _random_signal_factory(seed: int):
    rng = random.Random(seed)

    def _random_signal(bars: list[MarketBar], config: RunnerStrategyConfig) -> Signal:
        direction = "LONG" if rng.random() < 0.5 else "SHORT"
        return Signal(symbol=bars[-1].symbol, strategy_name="runner", direction=direction,
                      timestamp=bars[-1].time, rationale=f"random seed={seed}")

    return _random_signal


async def _fetch_symbol_info() -> SymbolInfo:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store
        return await McpMarketDataSource(client).get_symbol_info(SYMBOL)


async def _run(bars, symbol_info) -> dict:
    ledger = await run_backtest(
        bars=bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
        symbol_info=symbol_info, grid_config=GRID_CONFIG, runner_config=RUNNER_CONFIG,
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
        caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
        grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC, cycle_interval_bars=CYCLE_INTERVAL_BARS,
    )
    runner_trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
    grid_trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
    return {"runner": _stats(runner_trades), "grid": _stats(grid_trades)}


def _stats(trades: list[ClosedTrade]) -> dict:
    if not trades:
        return {"trade_count": 0, "expectancy_r": None, "max_drawdown_r": None,
                "win_rate": None, "profit_factor": None, "min_sample_met": False}
    return {
        "trade_count": len(trades),
        "expectancy_r": expectancy_r(trades),
        "max_drawdown_r": max_drawdown_r(trades),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "min_sample_met": has_minimum_sample(trades),
    }


def _fmt(v: Optional[float], suffix: str = "") -> str:
    return f"{v:+.3f}{suffix}" if v is not None else "n/a"


def _row(label: str, s: dict) -> str:
    if s["trade_count"] == 0:
        return f"  {label:>18}: no closed trades"
    return (f"  {label:>18}: n={s['trade_count']:>5} expectancy={_fmt(s['expectancy_r'], ' R')} "
            f"win_rate={_fmt(s['win_rate'])} pf={_fmt(s['profit_factor'])} "
            f"maxdd={_fmt(s['max_drawdown_r'], ' R')} min_sample_met={s['min_sample_met']}")


def _chronological_thirds(bars: list[MarketBar]) -> dict[str, list[MarketBar]]:
    n = len(bars)
    a, b = n // 3, 2 * n // 3
    return {"EARLY": bars[:a], "MIDDLE": bars[a:b], "RECENT": bars[b:]}


async def main() -> None:
    print("Fetching real XAUUSD SymbolInfo (one read-only live call) ...")
    symbol_info = await _fetch_symbol_info()
    print(f"SymbolInfo: digits={symbol_info.digits} point={symbol_info.point} "
          f"volume_min/max/step={symbol_info.volume_min}/{symbol_info.volume_max}/"
          f"{symbol_info.volume_step} stops_level={symbol_info.stops_level} "
          f"freeze_level={symbol_info.freeze_level} filling_modes={symbol_info.filling_modes} "
          f"spread={symbol_info.spread}")

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    all_bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not all_bars:
        raise RuntimeError(f"No cached bars at {path} -- run Step 1's cache seed first")

    windows = _chronological_thirds(all_bars)
    for label, bars in windows.items():
        print(f"{label} window: {len(bars)} bars, {bars[0].time} -> {bars[-1].time}")

    overall_summary = []

    for label, bars in windows.items():
        print(f"\n\n================ WINDOW: {label} ================")

        print("\n--- REAL runner (MACD-sign signal), production defaults ---")
        real_result = await _run(bars, symbol_info)
        print(_row("runner (real)", real_result["runner"]))
        print(_row("grid (reference)", real_result["grid"]))

        print(f"\n--- RUNNER RANDOM-DIRECTION baseline, {len(SEEDS)} seeds ---")
        random_results = []
        for seed in SEEDS:
            with patch(
                "mt5_mcp_trading.pipeline.runner_cycle.runner_signal",
                new=_random_signal_factory(seed),
            ):
                r = await _run(bars, symbol_info)
            random_results.append(r["runner"])
            print(f"  seed={seed:>2}: trades={r['runner']['trade_count']:>5} "
                  f"expectancy={_fmt(r['runner']['expectancy_r'], ' R')} "
                  f"win_rate={_fmt(r['runner']['win_rate'])}")

        exp_vals = [r["expectancy_r"] for r in random_results if r["expectancy_r"] is not None]

        def _band(vals):
            if not vals:
                return "n/a"
            mean = statistics.mean(vals)
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            return f"mean={mean:+.3f} sd={sd:.3f} range=[{min(vals):+.3f}, {max(vals):+.3f}]"

        print(f"\n  RANDOM expectancy_r: {_band(exp_vals)}")

        real_exp = real_result["runner"]["expectancy_r"]
        if exp_vals and real_exp is not None:
            rmean = statistics.mean(exp_vals)
            rsd = statistics.pstdev(exp_vals) if len(exp_vals) > 1 else 0.0
            z = (real_exp - rmean) / rsd if rsd > 0 else float("nan")
            outside_range = real_exp < min(exp_vals) or real_exp > max(exp_vals)
            print(f"\n  REAL vs RANDOM expectancy: real={real_exp:+.3f} R, random_mean={rmean:+.3f} R, "
                  f"delta={real_exp - rmean:+.3f} R, z={z:.2f}, "
                  f"real_outside_random_range={outside_range}")

        overall_summary.append((label, real_result, exp_vals))

    print("\n\n=== CROSS-WINDOW SUMMARY (runner) ===")
    for label, real_result, exp_vals in overall_summary:
        re = real_result["runner"]["expectancy_r"]
        rmean = statistics.mean(exp_vals) if exp_vals else None
        print(f"{label:>10}: real_expectancy={_fmt(re, ' R')}  random_mean_expectancy={_fmt(rmean, ' R')}  "
              f"real_trades={real_result['runner']['trade_count']}")

    print("\nNo production parameter adopted by this script. No Step 7 run. No Live Pilot work.")
    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
