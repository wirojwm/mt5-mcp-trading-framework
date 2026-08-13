#!/usr/bin/env python3
"""
Phase 8 continuation, Experiment 2 (approved 2026-08-13): does runner's MACD-sign signal carry
any genuine directional edge over a same-cost, same-risk random-direction baseline?

Fixed config, matching the approved plan exactly -- NOT a sweep, nothing tuned here:
- runner: current production defaults except the SL/TP floor, held at the previously-established
  apples-to-apples value (min_stop_distance_fraction_of_price=0.0) -- same config already used
  throughout this investigation's live-vs-backtest comparisons.
- max_concurrent_positions=1 -- Experiment 1's own finding was that concurrency doesn't change
  per-trade expectancy/win-rate/profit-factor (flat across cap 1/2/3/5 on the large NO-FLOOR
  sample), so cap=1 (today's real production value) is used here rather than introducing a second
  varying factor into an edge test that's supposed to isolate one question.
- fast=12/slow=26 (production default), atr_period=14, sl_atr_mult=3.0/tp_atr_mult=6.0 -- untouched.

RANDOM-DIRECTION BASELINE: a runtime monkeypatch of runner_cycle.runner_signal (nothing on disk
touched) that ignores MACD and returns LONG/SHORT with equal 0.5 probability, using a seeded RNG.
Never returns FLAT -- matches this dataset's own observed real-signal behavior (Experiment 1's
own logs: FLAT count was 0 in every real run across all three windows; MACD's sign was
directional every single cycle here), so the baseline isn't unfairly diluted by a FLAT rate the
real signal doesn't actually have. Everything else -- entry cadence (cycle_interval_bars=5),
SL/TP distance formula, position sizing, exposure/position-limit guards, spread cost model -- is
IDENTICAL between the real and random runs; only the direction-selection call differs.

20 pre-registered seeds (range(20), fixed before running, never expanded or cherry-picked after
seeing results) give a Monte Carlo distribution for the baseline in each window, reported as
mean +/- population stdev plus [min, max] -- the confidence/uncertainty band this experiment's
plan asked for.

THREE windows, all previously established, none re-split or newly chosen for this experiment:
- TRAIN (2026-05-30 -> 2026-07-22 22:46, Phase 8 Step 5's own window)
- HELD-OUT (2026-07-22 22:47 -> 2026-08-05 08:00, Phase 8 Step 6's own window)
- LIVE (2026-08-05 08:00 -> 2026-08-13 10:35, the real live-trading period, already used in this
  session's own live-window-backtest diagnostic) -- the "at least one additional independent
  window" the approved plan asked for; genuinely independent, never used to tune anything.

No "naive always-long/always-short" baseline added -- the random-direction baseline is already
direction-neutral by construction (50/50 long/short), satisfying the plan's "if already
supported... without inventing a new research branch" instruction.

READ-ONLY: one get_symbol_info call. BACKTEST/OFFLINE ONLY beyond that -- no MCP trading call, no
Step 7, no Live Pilot, no production default changed anywhere (every RunnerStrategyConfig here is
local; the monkeypatch is restored before this script exits either way, via try/finally).
"""

from __future__ import annotations

import asyncio
import dataclasses
import random
import statistics
import sys
from datetime import datetime, timezone
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
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig, runner_signal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state_backtest_symbol_info_probe"
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
CYCLE_INTERVAL_BARS = 5

TRAIN_END = datetime(2026, 7, 22, 22, 47, 0, tzinfo=timezone.utc)
TEST_END = datetime(2026, 8, 5, 8, 0, 0, tzinfo=timezone.utc)
SEEDS = list(range(20))  # pre-registered, fixed before running

RUNNER_CONFIG = RunnerStrategyConfig(min_stop_distance_fraction_of_price=0.0, max_concurrent_positions=1)


def _random_signal_factory(seed: int):
    rng = random.Random(seed)

    def _random_signal(bars: list[MarketBar], config: RunnerStrategyConfig) -> Signal:
        direction = "LONG" if rng.random() < 0.5 else "SHORT"  # never FLAT -- see module docstring
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
        symbol_info=symbol_info, grid_config=GridStrategyConfig(), runner_config=RUNNER_CONFIG,
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
        caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
        grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC, cycle_interval_bars=CYCLE_INTERVAL_BARS,
    )
    trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
    still_open = sum(1 for p in ledger.open_positions.values() if p.magic == RUNNER_MAGIC)
    holding = [(t.closed_at - t.opened_at).total_seconds() / 60 for t in trades]
    return {
        "trade_count": len(trades),
        "still_open": still_open,
        "expectancy_r": expectancy_r(trades) if trades else None,
        "max_drawdown_r": max_drawdown_r(trades) if trades else None,
        "win_rate": win_rate(trades) if trades else None,
        "profit_factor": profit_factor(trades) if trades else None,
        "min_sample_met": has_minimum_sample(trades) if trades else False,
        "avg_holding_min": statistics.mean(holding) if holding else None,
        "median_holding_min": statistics.median(holding) if holding else None,
    }


def _fmt(v: Optional[float], suffix: str = "") -> str:
    return f"{v:+.3f}{suffix}" if v is not None else "n/a"


async def main() -> None:
    print("Fetching real SymbolInfo (one read-only live call) ...")
    symbol_info = await _fetch_symbol_info()

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    all_bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not all_bars:
        raise RuntimeError(f"No cached bars at {path}")

    train_bars = [b for b in all_bars if b.time < TRAIN_END]
    test_bars = [b for b in all_bars if TRAIN_END <= b.time <= TEST_END]
    live_bars = [b for b in all_bars if b.time > TEST_END]
    windows = {"TRAIN": train_bars, "HELD-OUT": test_bars, "LIVE": live_bars}
    for label, bars in windows.items():
        print(f"{label} window: {len(bars)} bars, {bars[0].time} -> {bars[-1].time}")

    overall_summary = []

    for label, bars in windows.items():
        print(f"\n\n================ WINDOW: {label} ================")

        print("\n--- REAL runner (MACD-sign signal) ---")
        real_result = await _run(bars, symbol_info)
        print(f"  trades={real_result['trade_count']} still_open={real_result['still_open']} "
              f"expectancy={_fmt(real_result['expectancy_r'], ' R')} "
              f"drawdown={real_result['max_drawdown_r']} win_rate={real_result['win_rate']} "
              f"profit_factor={real_result['profit_factor']} "
              f"min_sample_met={real_result['min_sample_met']} "
              f"avg_hold_min={real_result['avg_holding_min']} "
              f"median_hold_min={real_result['median_holding_min']}")

        print(f"\n--- RANDOM-DIRECTION baseline, {len(SEEDS)} seeds ---")
        random_results = []
        for seed in SEEDS:
            with patch(
                "mt5_mcp_trading.pipeline.runner_cycle.runner_signal",
                new=_random_signal_factory(seed),
            ):
                r = await _run(bars, symbol_info)
            random_results.append(r)
            print(f"  seed={seed:>2}: trades={r['trade_count']:>5} "
                  f"expectancy={_fmt(r['expectancy_r'], ' R')} "
                  f"win_rate={r['win_rate']}")

        exp_vals = [r["expectancy_r"] for r in random_results if r["expectancy_r"] is not None]
        wr_vals = [r["win_rate"] for r in random_results if r["win_rate"] is not None]
        pf_vals = [r["profit_factor"] for r in random_results if r["profit_factor"] is not None]
        dd_vals = [r["max_drawdown_r"] for r in random_results if r["max_drawdown_r"] is not None]
        tc_vals = [r["trade_count"] for r in random_results]

        def _band(vals):
            if not vals:
                return "n/a"
            mean = statistics.mean(vals)
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            return f"mean={mean:+.3f} sd={sd:.3f} range=[{min(vals):+.3f}, {max(vals):+.3f}]"

        print(f"\n  RANDOM expectancy_r: {_band(exp_vals)}")
        print(f"  RANDOM win_rate:     {_band(wr_vals)}")
        print(f"  RANDOM profit_factor:{_band(pf_vals)}")
        print(f"  RANDOM max_drawdown_r:{_band(dd_vals)}")
        print(f"  RANDOM trade_count:  mean={statistics.mean(tc_vals):.0f} "
              f"range=[{min(tc_vals)}, {max(tc_vals)}]")

        if exp_vals and real_result["expectancy_r"] is not None:
            re = real_result["expectancy_r"]
            rmean = statistics.mean(exp_vals)
            rsd = statistics.pstdev(exp_vals) if len(exp_vals) > 1 else 0.0
            z = (re - rmean) / rsd if rsd > 0 else float("nan")
            outside_range = re < min(exp_vals) or re > max(exp_vals)
            print(f"\n  REAL vs RANDOM expectancy: real={re:+.3f} R, random_mean={rmean:+.3f} R, "
                  f"delta={re - rmean:+.3f} R, z={z:.2f}, "
                  f"real_outside_random_range={outside_range}")

        overall_summary.append((label, real_result, exp_vals, wr_vals, pf_vals))

    print("\n\n=== CROSS-WINDOW SUMMARY ===")
    for label, real_result, exp_vals, wr_vals, pf_vals in overall_summary:
        re = real_result["expectancy_r"]
        rmean = statistics.mean(exp_vals) if exp_vals else None
        print(f"{label:>10}: real_expectancy={_fmt(re, ' R')}  random_mean_expectancy={_fmt(rmean, ' R')}  "
              f"real_trades={real_result['trade_count']}")

    print("\n=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
