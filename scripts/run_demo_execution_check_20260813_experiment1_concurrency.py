#!/usr/bin/env python3
"""
Phase 8 continuation, Experiment 1 (approved 2026-08-13): does
RunnerStrategyConfig.max_concurrent_positions=1 act as an artificial design bottleneck that
materially distorts runner's opportunity set and performance?

Pre-specified concurrency set, fixed BEFORE running (not expanded after seeing results):
{1 (current production), 2, 3, 5}.

Two config variants tested at every concurrency level, per the approved plan's rule 7:
- "floor" = today's actual production config (min_stop_distance_fraction_of_price=0.01, the
  post-2026-08-11 fix).
- "no-floor" = the previously-established apples-to-apples config
  (min_stop_distance_fraction_of_price=0.0), used because the floor config alone produces near-
  zero closed trades in every window tried so far (see
  run_demo_execution_check_20260813_live_window_backtest.py) -- too thin a sample to see a
  concurrency effect on its own.

Two windows, reproducing the EXACT boundaries already established and validated in Phase 8 Step
5/6 (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md) and re-confirmed by this project's own
Step-6-script re-run today -- not a fresh 80/20 split of the now-larger (post-reseed) cache,
which would silently shift the boundary:
- TEST_START = 2026-07-22T22:47:00Z (test window's first bar, printed verbatim by
  run_demo_execution_backtest_test_window_validation.py's own log output today)
- TEST_END   = 2026-08-05T08:00:00Z (test window's last bar, same source)
- TRAIN window = every cached bar strictly before TEST_START.
Neither window is optimized on -- the concurrency set was fixed before this script ran, per the
approved plan's rule 4.

All other RunnerStrategyConfig fields stay at production defaults (sl_atr_mult=3.0,
tp_atr_mult=6.0, atr_period=14, fast=12, slow=26, min_stop_distance_points=10.0) -- ONLY
max_concurrent_positions and the floor field vary. Grid runs alongside with its own untouched
defaults, mirroring every established backtest script's methodology exactly (grid/runner
exposure tracking is per-magic, so this doesn't cross-contaminate runner's own results).

Signal/admission counting: a logging.Handler attached to
"mt5_mcp_trading.pipeline.runner_cycle" (never modifying that module itself) categorizes every
[RUNNER] log line into FLAT / rejected-by-position-limit / rejected-by-exposure-cap /
rejected-other / could-not-plan. total_cycles is computed structurally from the same loop shape
run_backtest() uses internally. admitted_signals is cross-checked against the ledger's actual
trade_count + still-open count as a sanity check, printed explicitly.

READ-ONLY: one get_symbol_info call, same pattern as every other Phase 8 backtest script.
BACKTEST/OFFLINE ONLY beyond that -- no MCP trading call, no Step 7, no Live Pilot, no production
default changed anywhere in this file (every RunnerStrategyConfig instance built here is local).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.engine import run_backtest
from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars
from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r, profit_factor, win_rate
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.domain.models import SymbolInfo
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state_backtest_symbol_info_probe"  # unused, read-only
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
CYCLE_INTERVAL_BARS = 5

TEST_START = datetime(2026, 7, 22, 22, 47, 0, tzinfo=timezone.utc)
TEST_END = datetime(2026, 8, 5, 8, 0, 0, tzinfo=timezone.utc)

CONCURRENCY_LEVELS = [1, 2, 3, 5]  # pre-specified, fixed before running -- do not expand post hoc


class RunnerCycleCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.flat = 0
        self.rejected_position_limit = 0
        self.rejected_exposure_cap = 0
        self.rejected_other = 0
        self.could_not_plan = 0

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "[RUNNER]" not in msg:
            return
        if "FLAT, no action" in msg:
            self.flat += 1
        elif "rejected (" in msg:
            if "position_limit" in msg:
                self.rejected_position_limit += 1
            elif "max_open_lots" in msg:
                self.rejected_exposure_cap += 1
            else:
                self.rejected_other += 1
        elif "could not be planned" in msg:
            self.could_not_plan += 1

    @property
    def rejected_total(self) -> int:
        return self.rejected_position_limit + self.rejected_exposure_cap + self.rejected_other + self.could_not_plan

    @property
    def directional_signals(self) -> int:
        return self.flat_complement

    flat_complement = 0  # set externally once total_cycles is known


def _total_cycles(n_bars: int, bars_count: int, cycle_interval_bars: int) -> int:
    start = bars_count - 1
    return len(range(start, n_bars, cycle_interval_bars))


def _max_concurrent(trades: list[ClosedTrade], still_open_opened_at: list[datetime], window_end: datetime) -> int:
    events: list[tuple[datetime, int]] = []
    for t in trades:
        events.append((t.opened_at, 1))
        events.append((t.closed_at, -1))
    for opened_at in still_open_opened_at:
        events.append((opened_at, 1))
        events.append((window_end, -1))  # artificial close at window end, for overlap counting only
    events.sort(key=lambda e: (e[0], -e[1]))  # opens before closes at the exact same instant
    running = 0
    peak = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


async def _fetch_symbol_info() -> SymbolInfo:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store
        return await McpMarketDataSource(client).get_symbol_info(SYMBOL)


async def _run_one(bars, symbol_info, runner_config: RunnerStrategyConfig, window_end: datetime) -> dict:
    counter = RunnerCycleCounter()
    runner_logger = logging.getLogger("mt5_mcp_trading.pipeline.runner_cycle")
    prev_level = runner_logger.level
    runner_logger.setLevel(logging.INFO)
    runner_logger.addHandler(counter)
    try:
        ledger = await run_backtest(
            bars=bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=GridStrategyConfig(), runner_config=runner_config,
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
    finally:
        runner_logger.removeHandler(counter)
        runner_logger.setLevel(prev_level)

    trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
    still_open = [p for p in ledger.open_positions.values() if p.magic == RUNNER_MAGIC]

    total_cycles = _total_cycles(len(bars), BARS_COUNT, CYCLE_INTERVAL_BARS)
    directional_signals = total_cycles - counter.flat
    admitted_inferred = directional_signals - counter.rejected_total
    admitted_actual = len(trades) + len(still_open)  # sanity cross-check

    holding_minutes = [(t.closed_at - t.opened_at).total_seconds() / 60 for t in trades]
    peak_concurrent = _max_concurrent(trades, [p.opened_at for p in still_open], window_end)

    return {
        "trades": trades,
        "trade_count": len(trades),
        "still_open": len(still_open),
        "total_cycles": total_cycles,
        "flat": counter.flat,
        "directional_signals": directional_signals,
        "rejected_position_limit": counter.rejected_position_limit,
        "rejected_exposure_cap": counter.rejected_exposure_cap,
        "rejected_other": counter.rejected_other,
        "could_not_plan": counter.could_not_plan,
        "admitted_inferred": admitted_inferred,
        "admitted_actual": admitted_actual,
        "pct_directional_blocked_by_concurrency": (
            100 * counter.rejected_position_limit / directional_signals if directional_signals else 0.0
        ),
        "holding_minutes": holding_minutes,
        "peak_concurrent": peak_concurrent,
        "expectancy_r": expectancy_r(trades) if trades else None,
        "max_drawdown_r": max_drawdown_r(trades) if trades else None,
        "win_rate": win_rate(trades) if trades else None,
        "profit_factor": profit_factor(trades) if trades else None,
        "min_sample_met": has_minimum_sample(trades) if trades else False,
    }


def _print_result(label: str, r: dict) -> None:
    print(f"\n--- {label} ---")
    print(f"  total cycles evaluated: {r['total_cycles']}   FLAT: {r['flat']}   "
          f"directional signals: {r['directional_signals']}")
    print(f"  rejected -- position_limit: {r['rejected_position_limit']}   "
          f"exposure_cap: {r['rejected_exposure_cap']}   other: {r['rejected_other']}   "
          f"could_not_plan: {r['could_not_plan']}")
    print(f"  admitted (inferred from log counts): {r['admitted_inferred']}   "
          f"admitted (actual, trades+still_open): {r['admitted_actual']}   "
          f"{'OK, matches' if r['admitted_inferred'] == r['admitted_actual'] else 'MISMATCH -- investigate'}")
    print(f"  % of directional signals blocked by concurrency cap: "
          f"{r['pct_directional_blocked_by_concurrency']:.1f}%")
    print(f"  peak simultaneous open runner positions: {r['peak_concurrent']}")
    print(f"  trade_count: {r['trade_count']}   still_open_at_window_end: {r['still_open']}")
    if r["holding_minutes"]:
        hm = r["holding_minutes"]
        print(f"  holding time (min): mean={statistics.mean(hm):.1f}  "
              f"median={statistics.median(hm):.1f}  max={max(hm):.1f}")
    else:
        print("  holding time (min): n/a (no closed trades)")
    if r["trade_count"] > 0:
        print(f"  expectancy_r: {r['expectancy_r']:+.3f} R   max_drawdown_r: {r['max_drawdown_r']:.3f} R   "
              f"win_rate: {r['win_rate']:.1%}   profit_factor: {r['profit_factor']:.3f}   "
              f"min_sample_met(30+): {r['min_sample_met']}")
    else:
        print("  expectancy/drawdown/win_rate/profit_factor: n/a (no closed trades)")


async def main() -> None:
    print("Fetching real SymbolInfo (one read-only live call) ...")
    symbol_info = await _fetch_symbol_info()

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    all_bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not all_bars:
        raise RuntimeError(f"No cached bars at {path}")

    train_bars = [b for b in all_bars if b.time < TEST_START]
    test_bars = [b for b in all_bars if TEST_START <= b.time <= TEST_END]
    if not train_bars or not test_bars:
        raise RuntimeError("Train/test window filter produced an empty window -- check cache coverage")

    windows = {
        "TRAIN": (train_bars, TEST_START),
        "HELD-OUT/TEST": (test_bars, TEST_END),
    }
    print(f"TRAIN window: {len(train_bars)} bars, {train_bars[0].time} -> {train_bars[-1].time}")
    print(f"HELD-OUT/TEST window: {len(test_bars)} bars, {test_bars[0].time} -> {test_bars[-1].time} "
          f"(exact boundaries reproduced from Phase 8 Step 6, not re-split)")

    config_variants = {
        "FLOOR (current production, min_stop_distance_fraction_of_price=0.01)": 0.01,
        "NO-FLOOR (apples-to-apples, min_stop_distance_fraction_of_price=0.0)": 0.0,
    }

    summary_rows: list[tuple[str, str, int, dict]] = []

    for window_label, (bars, window_end) in windows.items():
        for variant_label, floor_value in config_variants.items():
            for cap in CONCURRENCY_LEVELS:
                runner_config = RunnerStrategyConfig(
                    min_stop_distance_fraction_of_price=floor_value,
                    max_concurrent_positions=cap,
                )
                label = f"{window_label} | {variant_label} | max_concurrent_positions={cap}"
                print(f"\nRunning: {label} ...")
                result = await _run_one(bars, symbol_info, runner_config, window_end)
                _print_result(label, result)
                summary_rows.append((window_label, variant_label, cap, result))

    print("\n\n=== SUMMARY TABLE ===")
    header = (f"{'window':<15}{'variant':<12}{'cap':>4}{'trades':>8}{'open':>6}{'blocked%':>10}"
              f"{'peak_conc':>10}{'exp_R':>9}{'dd_R':>9}{'win%':>7}{'PF':>7}")
    print(header)
    for window_label, variant_label, cap, r in summary_rows:
        short_variant = "FLOOR" if variant_label.startswith("FLOOR") else "NO-FLOOR"
        exp = f"{r['expectancy_r']:+.3f}" if r["expectancy_r"] is not None else "n/a"
        dd = f"{r['max_drawdown_r']:.3f}" if r["max_drawdown_r"] is not None else "n/a"
        wr = f"{100 * r['win_rate']:.1f}" if r["win_rate"] is not None else "n/a"
        pf = f"{r['profit_factor']:.3f}" if r["profit_factor"] is not None else "n/a"
        print(f"{window_label:<15}{short_variant:<12}{cap:>4}{r['trade_count']:>8}{r['still_open']:>6}"
              f"{r['pct_directional_blocked_by_concurrency']:>9.1f}%{r['peak_concurrent']:>10}"
              f"{exp:>9}{dd:>9}{wr:>7}{pf:>7}")

    print("\n=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
