#!/usr/bin/env python3
"""
Phase 8 continuation, Experiment 5 (design approved 2026-08-13, execution approved 2026-08-14,
docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md "Experiment 5" section): does the M15/H1
single-bar mean-reversion structure Experiment 4 found (a runs-test effect at the 1-bar horizon,
Bonferroni-significant and same-sign in all three chronological-thirds windows, for both
timeframes) survive as a real, cost-inclusive economic edge -- or is it structure that doesn't
translate into tradeable expectancy?

SIGNAL (zero free parameters): "fade the last closed bar" -- SHORT if that bar's own return was
positive, LONG if negative. The most mechanically direct possible translation of the runs-test
statistic itself (a same-bar sign-reversal count) into a trading rule.

EXIT (the one design decision that needed explicit sign-off before this script existed): a fixed
1-bar holding period -- enter at bar i's close, force-close at bar i+1's close, regardless of
price path in between. This does NOT reuse runner's ATR-bracket exit (built for a longer-hold
momentum signal, would bury a confirmed 1-bar effect under multi-bar drift) or
pipeline/runner_cycle.py's cycle-throttled entry cadence (every bar gets its own signal here, by
design -- this is a different trading rule being tested, not a runner-config variant).

HARNESS SCOPE (kept deliberately additive, per the approved design -- no change to
backtest/engine.py, pipeline/, strategy/, or execution/): this script drives
backtest.engine.ReplayCursor / BacktestOrderExecutor / BacktestLedger directly, bypassing
backtest.engine.run_backtest()'s own pipeline-cycle orchestration (run_grid_cycle/
run_runner_cycle) entirely, since neither of those functions can express "close this position on
the very next bar no matter what." strategy.runner.compute_stop_distances() IS reused unmodified
to size a NOMINAL ATR-based SL/TP for every entry: sl_atr_mult=tp_atr_mult=50 (vs. production's
3.0/6.0), plus min_stop_distance_fraction_of_price=0.05 (vs. production's 0.01) as a floor for
low-ATR bars -- both chosen to be wide enough that the nominal stop should essentially never
trigger inside a single bar, existing ONLY to (a) satisfy the real McpOrderExecutor's non-zero
SL/TP requirement this backtest ledger structurally mirrors (BacktestLedger.close_position()
raises if sl == price_open) and (b) give ClosedTrade.r_multiple a well-defined, ATR-scaled
denominator. BacktestOrderExecutor.check_fills_and_exits() IS still called every bar (unmodified,
real engine code) so a same-bar SL/TP breach -- if the nominal stop somehow isn't wide enough --
still closes correctly rather than being silently overridden; the forced 1-bar exit only fires
for positions check_fills_and_exits() left open. Each run reports how many trades closed "MANUAL"
(the intended 1-bar exit) vs "SL"/"TP" (nominal-stop leakage) so any contamination is visible,
not assumed away.

BASELINES: random-direction (20 pre-registered seeds, same shape/RNG convention as Experiment 2),
always-long, always-short -- the two "naive directional" baselines Experiment 2 judged unnecessary
there (that random baseline was already direction-neutral) but explicitly scoped INTO this
design, since always-long/always-short are exactly what "fade the last bar" must beat to prove the
edge is in the fading logic and not just being long or short BTC over this history.

WINDOWS: Experiment 4's own EARLY/MIDDLE/RECENT chronological thirds, per timeframe, reused
verbatim -- no new split invented for this experiment.

COSTS: engine.py's existing spread-only model, every run done twice (spread_multiplier=0 and =1)
so cost drag is read directly off the same run pair rather than estimated.

CLASSIFICATION (pre-defined in the checkpoint doc, restated here so this script's output maps
directly onto it):
  A - robust economic edge: consistent (same-sign, real > all three baseline means) across all
      three windows, AND survives spread_multiplier=1. A single good window cannot promote to A.
  B - weak/inconclusive: some support but not full 3-window consistency, or beats baselines only
      narrowly/inconsistently.
  C - structure exists at zero-cost, real-cost erases it (real beats baselines at
      spread_multiplier=0 in a way it does not at spread_multiplier=1).
  D - no usable edge either way, cost or no cost.

OFFLINE ONLY, per this session's explicit instruction: no MCP call of any kind (unlike Experiment
2's one read-only get_symbol_info call) -- reuses the same static, previously-live-fetched BTCUSD
SymbolInfo already on record in docs/MCP_ADAPTER_WIRING_CHECKPOINT.md and reused by
scripts/run_demo_execution_backtest_regime_filter_test_window_validation_0013.py. No Step 7, no
Live Pilot, no production RunnerStrategyConfig/GridStrategyConfig default changed -- every config
instance below is local to this script.
"""

from __future__ import annotations

import asyncio
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from mt5_mcp_trading.backtest.engine import BacktestOrderExecutor, ReplayCursor, half_spread_price
from mt5_mcp_trading.backtest.ledger import BacktestLedger
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars
from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r, profit_factor, win_rate
from mt5_mcp_trading.domain.models import MarketBar, OrderPlan, SymbolInfo
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig, compute_stop_distances

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"
SYMBOL = "BTCUSD"
MAGIC = 75105  # exp5-only, never a production magic (grid=71101, runner=72101)
VOLUME = 0.01
SEEDS = list(range(20))  # pre-registered, same convention as Experiment 2

# Real BTCUSD symbol constraints, fetched live once and documented in
# docs/MCP_ADAPTER_WIRING_CHECKPOINT.md -- reused here (not re-fetched), per this session's
# offline-only instruction.
SYMBOL_INFO = SymbolInfo(
    symbol="BTCUSD", digits=2, point=0.01, volume_min=0.01, volume_max=5.0, volume_step=0.01,
    stops_level=10, freeze_level=0, filling_modes=("FOK",), spread=1500,
)

# Nominal-only -- see module docstring. NOT a production runner config; sized to (almost) never
# trigger inside one bar, purely so ClosedTrade.r_multiple has a well-defined ATR-scaled
# denominator and the ledger's non-zero-risk invariant holds.
NOMINAL_CONFIG = RunnerStrategyConfig(
    atr_period=14, sl_atr_mult=50.0, tp_atr_mult=50.0, min_stop_distance_fraction_of_price=0.05,
)

SignalFn = Callable[[MarketBar, MarketBar, int], str]  # (bar, prev_bar, index) -> "LONG"/"SHORT"


def fade_last_bar(bar: MarketBar, prev_bar: MarketBar, index: int) -> str:
    del index
    ret = bar.close - prev_bar.close
    if ret > 0:
        return "SHORT"
    if ret < 0:
        return "LONG"
    return "LONG"  # zero-return tie-break, arbitrary but fixed -- see module docstring


def always_long(bar: MarketBar, prev_bar: MarketBar, index: int) -> str:
    del bar, prev_bar, index
    return "LONG"


def always_short(bar: MarketBar, prev_bar: MarketBar, index: int) -> str:
    del bar, prev_bar, index
    return "SHORT"


def random_signal_factory(seed: int) -> SignalFn:
    rng = random.Random(seed)

    def _signal(bar: MarketBar, prev_bar: MarketBar, index: int) -> str:
        del bar, prev_bar, index
        return "LONG" if rng.random() < 0.5 else "SHORT"

    return _signal


@dataclass
class RunResult:
    trade_count: int
    manual_closes: int
    sl_tp_leaks: int
    expectancy_r: Optional[float]
    win_rate: Optional[float]
    profit_factor: Optional[float]
    max_drawdown_r: Optional[float]
    min_sample_met: bool


async def run_candidate(bars: list[MarketBar], signal_fn: SignalFn, spread_multiplier: float) -> RunResult:
    """Bar-by-bar fixed-1-bar-hold replay -- see module docstring for why this bypasses
    backtest.engine.run_backtest()'s own cycle orchestration but still drives its executor/ledger
    classes unmodified."""
    period = NOMINAL_CONFIG.atr_period
    start = period + 1
    if len(bars) < start + 2:
        return RunResult(0, 0, 0, None, None, None, None, False)

    cursor = ReplayCursor(bars)
    ledger = BacktestLedger()
    executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO, spread_multiplier)

    for i in range(start, len(bars)):
        cursor.index = i
        bar = bars[i]
        executor.check_fills_and_exits(bar)  # real engine code, unmodified -- see module docstring

        # Force-close whatever was opened on the PREVIOUS iteration -- this bar is that
        # position's "next bar", so this is exactly the fixed 1-bar exit the design calls for.
        # (A position closed above via check_fills_and_exits() is already gone from
        # open_positions and is simply skipped here.)
        for ticket in list(ledger.open_positions.keys()):
            await executor.close_position(ticket)

        if i == len(bars) - 1:
            break  # no next bar left to force-close a fresh entry against -- leave it unopened

        prev_bar = bars[i - 1]
        direction = signal_fn(bar, prev_bar, i)
        side = "BUY" if direction == "LONG" else "SELL"

        visible = bars[i - period : i + 1]  # exactly period+1 bars -- all compute_stop_distances needs
        sl_distance, tp_distance = compute_stop_distances(visible, SYMBOL_INFO.point, NOMINAL_CONFIG)
        half = half_spread_price(bar, SYMBOL_INFO, spread_multiplier)
        reference_price = bar.close + half if side == "BUY" else bar.close - half
        if side == "BUY":
            sl = round(reference_price - sl_distance, SYMBOL_INFO.digits)
            tp = round(reference_price + tp_distance, SYMBOL_INFO.digits)
        else:
            sl = round(reference_price + sl_distance, SYMBOL_INFO.digits)
            tp = round(reference_price - tp_distance, SYMBOL_INFO.digits)

        plan = OrderPlan(symbol=SYMBOL, order_type="MARKET", side=side, volume=VOLUME, price=None,
                          sl=sl, tp=tp, deviation=150, magic=MAGIC, comment="exp5_fade1bar")
        await executor.submit(plan)

    trades = ledger.closed_trades
    manual = sum(1 for t in trades if t.close_reason == "MANUAL")
    leaked = sum(1 for t in trades if t.close_reason in ("SL", "TP"))
    if not trades:
        return RunResult(0, manual, leaked, None, None, None, None, False)
    return RunResult(
        trade_count=len(trades), manual_closes=manual, sl_tp_leaks=leaked,
        expectancy_r=expectancy_r(trades), win_rate=win_rate(trades),
        profit_factor=profit_factor(trades), max_drawdown_r=max_drawdown_r(trades),
        min_sample_met=has_minimum_sample(trades),
    )


def _chronological_thirds(bars: list[MarketBar]) -> dict[str, list[MarketBar]]:
    n = len(bars)
    a, b = n // 3, 2 * n // 3
    return {"EARLY": bars[:a], "MIDDLE": bars[a:b], "RECENT": bars[b:]}


def _fmt(v: Optional[float], suffix: str = "") -> str:
    return f"{v:+.4f}{suffix}" if v is not None else "n/a"


def _band(vals: list[float]) -> str:
    if not vals:
        return "n/a"
    mean = statistics.mean(vals)
    sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return f"mean={mean:+.4f} sd={sd:.4f} range=[{min(vals):+.4f}, {max(vals):+.4f}]"


def _row(label: str, r: RunResult) -> str:
    if r.trade_count == 0:
        return f"  {label:>18}: no closed trades"
    leak_note = f" (SL/TP leaks={r.sl_tp_leaks})" if r.sl_tp_leaks else ""
    return (
        f"  {label:>18}: n={r.trade_count:>5} expectancy={_fmt(r.expectancy_r, ' R')} "
        f"win_rate={_fmt(r.win_rate)} pf={_fmt(r.profit_factor)} "
        f"maxdd={_fmt(r.max_drawdown_r, ' R')} min_sample_met={r.min_sample_met}{leak_note}"
    )


async def _analyze_timeframe(timeframe: str) -> dict:
    path = cache_path(CACHE_DIR, SYMBOL, timeframe)
    all_bars = load_bars(path, SYMBOL, timeframe)
    if not all_bars:
        raise RuntimeError(f"No cached bars at {path} -- run the Experiment 4 cache seed first")

    windows = _chronological_thirds(all_bars)
    print(f"\n\n#################### CANDIDATE: {timeframe} single-bar mean-reversion "
          f"({len(all_bars)} bars, {all_bars[0].time} -> {all_bars[-1].time}) ####################")

    per_window_summary = {}

    for window_label, bars in windows.items():
        print(f"\n================ WINDOW: {window_label} "
              f"({len(bars)} bars, {bars[0].time} -> {bars[-1].time}) ================")

        per_cost_summary = {}
        for spread_multiplier in (0.0, 1.0):
            cost_label = "ZERO-COST" if spread_multiplier == 0.0 else "REAL-COST (spread x1)"
            print(f"\n--- {cost_label} ---")

            real = await run_candidate(bars, fade_last_bar, spread_multiplier)
            long_r = await run_candidate(bars, always_long, spread_multiplier)
            short_r = await run_candidate(bars, always_short, spread_multiplier)
            print(_row("REAL (fade)", real))
            print(_row("ALWAYS-LONG", long_r))
            print(_row("ALWAYS-SHORT", short_r))

            random_results = [await run_candidate(bars, random_signal_factory(s), spread_multiplier) for s in SEEDS]
            exp_vals = [r.expectancy_r for r in random_results if r.expectancy_r is not None]
            print(f"  {'RANDOM (20 seeds)':>18}: expectancy {_band(exp_vals)}")

            real_exp = real.expectancy_r
            z = None
            outside_range = None
            if exp_vals and real_exp is not None:
                rmean = statistics.mean(exp_vals)
                rsd = statistics.pstdev(exp_vals) if len(exp_vals) > 1 else 0.0
                z = (real_exp - rmean) / rsd if rsd > 0 else float("nan")
                outside_range = real_exp < min(exp_vals) or real_exp > max(exp_vals)
                print(f"  REAL vs RANDOM: delta={real_exp - rmean:+.4f} R  z={z:.2f}  "
                      f"outside_random_range={outside_range}")
            beats_long = real_exp is not None and long_r.expectancy_r is not None and real_exp > long_r.expectancy_r
            beats_short = real_exp is not None and short_r.expectancy_r is not None and real_exp > short_r.expectancy_r
            beats_random = real_exp is not None and bool(exp_vals) and real_exp > statistics.mean(exp_vals)
            print(f"  REAL beats ALWAYS-LONG={beats_long}  ALWAYS-SHORT={beats_short}  "
                  f"RANDOM-mean={beats_random}")

            per_cost_summary[spread_multiplier] = {
                "real_expectancy": real_exp, "beats_long": beats_long, "beats_short": beats_short,
                "beats_random": beats_random, "z_vs_random": z, "outside_random_range": outside_range,
                "trade_count": real.trade_count, "min_sample_met": real.min_sample_met,
            }

        per_window_summary[window_label] = per_cost_summary

    return per_window_summary


def _classify(timeframe: str, summary: dict) -> str:
    windows = list(summary.keys())
    zero_cost_all_beat = all(
        summary[w][0.0]["beats_long"] and summary[w][0.0]["beats_short"] and summary[w][0.0]["beats_random"]
        for w in windows
    )
    real_cost_all_beat = all(
        summary[w][1.0]["beats_long"] and summary[w][1.0]["beats_short"] and summary[w][1.0]["beats_random"]
        for w in windows
    )
    real_cost_signs = {
        w: (1 if summary[w][1.0]["real_expectancy"] is not None and summary[w][1.0]["real_expectancy"] > 0 else -1)
        for w in windows
    }
    real_cost_any_beat = any(
        summary[w][1.0]["beats_long"] and summary[w][1.0]["beats_short"] and summary[w][1.0]["beats_random"]
        for w in windows
    )
    min_sample_all = all(summary[w][1.0]["min_sample_met"] for w in windows)

    if real_cost_all_beat and min_sample_all:
        classification = "A"
        reason = "beats all three baselines in all three windows, survives spread_multiplier=1"
    elif zero_cost_all_beat and not real_cost_any_beat:
        classification = "C"
        reason = "beats all three baselines zero-cost in every window, but cost erases the edge in every window"
    elif real_cost_any_beat or zero_cost_all_beat:
        classification = "B"
        reason = "partial/inconsistent support -- beats baselines in some but not all windows/cost settings"
    else:
        classification = "D"
        reason = "does not beat baselines at zero cost in even a majority of windows"

    print(f"\n=== {timeframe} CLASSIFICATION: {classification} ===")
    print(f"  Reason: {reason}")
    print(f"  Zero-cost, all baselines beaten in all 3 windows: {zero_cost_all_beat}")
    print(f"  Real-cost (spread x1), all baselines beaten in all 3 windows: {real_cost_all_beat}")
    print(f"  Real-cost expectancy sign per window: {real_cost_signs}")
    print(f"  Min sample (>=30 trades) met in all 3 windows at real cost: {min_sample_all}")
    return classification


async def main() -> None:
    results = {}
    for timeframe in ["M15", "H1"]:
        summary = await _analyze_timeframe(timeframe)
        classification = _classify(timeframe, summary)
        results[timeframe] = classification

    print("\n\n=== EXPERIMENT 5 OVERALL SUMMARY ===")
    for timeframe, classification in results.items():
        recommendation = {
            "A": "KEEP FOR FURTHER RESEARCH",
            "B": "KEEP FOR FURTHER RESEARCH (weak -- needs more evidence before any adoption step)",
            "C": "ABANDON (structure real, but does not survive real transaction costs)",
            "D": "ABANDON",
        }[classification]
        print(f"  {timeframe}: classification={classification}  recommendation={recommendation}")
    print("\nNo production parameter adopted by this script. No Step 7 run. No Live Pilot work.")
    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
