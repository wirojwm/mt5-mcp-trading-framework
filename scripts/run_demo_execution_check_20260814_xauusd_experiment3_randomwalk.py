#!/usr/bin/env python3
"""
XAUUSD signal-edge research, Step 3 (approved 2026-08-14, docs/XAUUSD_SIGNAL_EDGE_CHECKPOINT.md):
is XAUUSD M1 price action distinguishable from a random walk? Runs directly because Step 2 (the
currently-deployed runner strategy's own edge test) came back INCONCLUSIVE rather than negative --
production runner generated only 1-4 closed trades per ~31-day window on XAUUSD (min_sample_met
=False every window), almost certainly the same sl_atr_mult=3.0 + 1%-floor +
max_concurrent_positions=1 lockup mechanism Experiment 1 already confirmed on BTCUSD. This step
sidesteps that bottleneck entirely -- it tests price action directly, needs no trade generation,
so the sample-size problem that stalled Step 2 does not apply here.

METHODOLOGY: `docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md` Experiment 3's exact test
code, reused verbatim (ACF / Lo-MacKinlay homoskedastic variance ratio / Wald-Wolfowitz runs
test on non-overlapping q-bar return signs), same five pre-specified fixed horizons
{1, 5, 15, 30, 60} bars, same Bonferroni-corrected alpha (0.05 / 45 tests) alongside the same
pre-committed cross-window-consistency requirement (same sign, significant in all three windows)
before anything counts as real structure. Zero free parameters -- the only thing that changed
from Experiment 3's own BTCUSD run is which cached CSV gets loaded.

WINDOWS: three chronological thirds (EARLY/MIDDLE/RECENT), NOT Experiment 3's calendar-based
TRAIN/HELD-OUT/LIVE split -- XAUUSD has never been live-traded by this project, so there is no
genuine "LIVE" period to carve out, exactly the same situation Experiment 4 already handled for
BTCUSD M15/H1 (which also had no live-trading history at those timeframes). Reuses Experiment 4's
`_chronological_thirds()` convention rather than inventing a new one.

FULLY OFFLINE -- reads only the cache seeded by Step 1
(scripts/run_demo_execution_check_20260814_xauusd_cache_seed.py). No MCP call of any kind in this
script, not even get_symbol_info. No production default touched, no strategy built or tuned.
"""

from __future__ import annotations

import math
from pathlib import Path

from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"
SYMBOL = "XAUUSD"
TIMEFRAME = "M1"

HORIZONS = [1, 5, 15, 30, 60]  # bars, reused verbatim from Experiments 3-4, fixed before running
ALPHA = 0.05
MINUTES_PER_BAR = 1  # M1


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _two_sided_p(z: float) -> float:
    return 2 * (1 - _normal_cdf(abs(z)))


def _acf(returns: list[float], lag: int) -> tuple[float, float, float]:
    n = len(returns)
    mean = sum(returns) / n
    num = sum((returns[t] - mean) * (returns[t + lag] - mean) for t in range(n - lag))
    den = sum((r - mean) ** 2 for r in returns)
    coef = num / den
    band = 1.96 / math.sqrt(n)
    z = coef / (1 / math.sqrt(n))
    p = _two_sided_p(z)
    return coef, band, p


def _variance_ratio(log_prices: list[float], q: int) -> tuple[float, float, float]:
    n = len(log_prices) - 1
    mu = (log_prices[-1] - log_prices[0]) / n
    sigma_a2 = sum((log_prices[k] - log_prices[k - 1] - mu) ** 2 for k in range(1, n + 1)) / (n - 1)
    m = q * (n - q + 1) * (1 - q / n)
    sigma_c2 = sum(
        (log_prices[k] - log_prices[k - q] - q * mu) ** 2 for k in range(q, n + 1)
    ) / m
    vr = sigma_c2 / sigma_a2
    theta = (2 * (2 * q - 1) * (q - 1)) / (3 * q * n) if q > 1 else 1e-12
    z = (vr - 1) / math.sqrt(theta) if theta > 0 else 0.0
    p = _two_sided_p(z) if q > 1 else 1.0
    return vr, z, p


def _runs_test(log_prices: list[float], q: int) -> tuple[int, int, int, float, float]:
    n = len(log_prices) - 1
    n_blocks = n // q
    signs = []
    for i in range(n_blocks):
        start, end = i * q, (i + 1) * q
        ret = log_prices[end] - log_prices[start]
        if ret > 0:
            signs.append(1)
        elif ret < 0:
            signs.append(-1)
    n1 = sum(1 for s in signs if s == 1)
    n2 = sum(1 for s in signs if s == -1)
    total = n1 + n2
    runs = 1 + sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
    e_r = 1 + 2 * n1 * n2 / total
    var_r = (2 * n1 * n2 * (2 * n1 * n2 - total)) / (total ** 2 * (total - 1))
    z = (runs - e_r) / math.sqrt(var_r) if var_r > 0 else 0.0
    p = _two_sided_p(z)
    return runs, n1, n2, z, p


def _chronological_thirds(bars):
    n = len(bars)
    a, b = n // 3, 2 * n // 3
    return {"EARLY": bars[:a], "MIDDLE": bars[a:b], "RECENT": bars[b:]}


def main() -> None:
    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    all_bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not all_bars:
        raise RuntimeError(f"No cached bars at {path} -- run Step 1's cache seed first")

    num_tests_total = 3 * len(HORIZONS) * 3
    bonferroni_alpha = ALPHA / num_tests_total

    print(f"\n\n#################### {SYMBOL} {TIMEFRAME} random-walk test "
          f"({len(all_bars)} bars, {all_bars[0].time} -> {all_bars[-1].time}) ####################")
    print(f"Multiple-comparisons context: {num_tests_total} tests. "
          f"Uncorrected alpha={ALPHA}, Bonferroni alpha={bonferroni_alpha:.6f}.")
    print("Horizon real-time equivalents: " +
          ", ".join(f"{h} bars={h * MINUTES_PER_BAR}min" for h in HORIZONS))

    windows = _chronological_thirds(all_bars)
    all_results: dict[str, dict] = {}

    for window_label, bars in windows.items():
        closes = [b.close for b in bars]
        log_prices = [math.log(c) for c in closes]
        returns = [log_prices[i] - log_prices[i - 1] for i in range(1, len(log_prices))]
        n = len(returns)
        print(f"\n================ WINDOW: {window_label} "
              f"({len(bars)} bars, {n} returns, {bars[0].time} -> {bars[-1].time}) ================")

        window_results = {"acf": {}, "vr": {}, "runs": {}}

        print("\n--- ACF ---")
        for lag in HORIZONS:
            coef, band, p = _acf(returns, lag)
            sig_raw = abs(coef) > band
            sig_bonf = p < bonferroni_alpha
            print(f"  lag={lag:>3}: ACF={coef:+.5f}  band=+/-{band:.5f}  p={p:.4f}  "
                  f"sig(raw)={sig_raw}  sig(bonf)={sig_bonf}")
            window_results["acf"][lag] = (coef, p, sig_raw, sig_bonf)

        print("\n--- Variance ratio (homoskedastic) ---")
        for q in HORIZONS:
            vr, z, p = _variance_ratio(log_prices, q)
            sig_raw = p < ALPHA
            sig_bonf = p < bonferroni_alpha
            direction = "momentum-like" if vr > 1 else ("mean-reversion-like" if vr < 1 else "neutral")
            print(f"  q={q:>3}: VR={vr:.4f}  z={z:+.2f}  p={p:.4f}  {direction}  "
                  f"sig(raw)={sig_raw}  sig(bonf)={sig_bonf}")
            window_results["vr"][q] = (vr, p, sig_raw, sig_bonf)

        print("\n--- Runs test ---")
        for q in HORIZONS:
            runs, n1, n2, z, p = _runs_test(log_prices, q)
            sig_raw = p < ALPHA
            sig_bonf = p < bonferroni_alpha
            direction = "fewer runs (momentum-like)" if z < 0 else "more runs (mean-reversion-like)"
            print(f"  q={q:>3}: runs={runs} (up={n1}, down={n2})  z={z:+.2f}  p={p:.4f}  "
                  f"{direction if sig_raw else 'no departure'}  "
                  f"sig(raw)={sig_raw}  sig(bonf)={sig_bonf}")
            window_results["runs"][q] = (z, p, sig_raw, sig_bonf)

        all_results[window_label] = window_results

    print(f"\n\n=== {SYMBOL} {TIMEFRAME} CROSS-WINDOW CONSISTENCY SUMMARY ===")
    centering = {"acf": lambda v: v, "vr": lambda v: v - 1.0, "runs": lambda v: v}
    for family, key in [("ACF", "acf"), ("Variance Ratio", "vr"), ("Runs test", "runs")]:
        print(f"--- {family} ---")
        center = centering[key]
        for h in HORIZONS:
            cells = []
            for w in windows:
                entry = all_results[w][key][h]
                sign = "+" if center(entry[0]) > 0 else "-"
                flag = "**" if entry[3] else ("*" if entry[2] else "")
                cells.append(f"{w}:{sign}{flag}")
            same_sign = len({("+" if center(all_results[w][key][h][0]) > 0 else "-") for w in windows}) == 1
            all_raw_sig = all(all_results[w][key][h][2] for w in windows)
            all_bonf_sig = all(all_results[w][key][h][3] for w in windows)
            consistent = same_sign and all_raw_sig
            print(f"  horizon={h:>3} ({h * MINUTES_PER_BAR}min): {'  '.join(cells)}   "
                  f"CONSISTENT={consistent}  bonferroni_sig_all={all_bonf_sig}")
        print()

    print("No production parameter adopted by this script. No Step 7 run. No Live Pilot work.")
    print("=====================================================================\n")


if __name__ == "__main__":
    main()
