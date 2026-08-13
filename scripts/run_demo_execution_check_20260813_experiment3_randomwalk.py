#!/usr/bin/env python3
"""
Phase 8 continuation, Experiment 3 (approved 2026-08-13): does BTCUSD M1 price action show any
statistically meaningful, economically relevant departure from a driftless random walk at
horizons matching runner's own observed holding times -- the direct test of Experiment 2's
"or is this just a random walk" branch, chosen over a signal-family search because it carries
zero parameter-search/overfitting risk (standard, off-the-shelf tests, no free parameters).

Three standard tests, each run at five PRE-SPECIFIED, FIXED horizons (bars) -- {1, 5, 15, 30, 60}
-- chosen once, before running, to span from microstructure noise up through and beyond runner's
own measured median/average holding times (Experiment 2: TRAIN 19.0/36.6 min, HELD-OUT
15.0/32.3 min, LIVE 12.0/24.9 min -- all comfortably inside this range). Not expanded or pruned
after seeing results.

1. ACF of 1-bar log returns at each horizon, tested against the standard white-noise band
   +/-1.96/sqrt(N).
2. Lo-MacKinlay (1988) homoskedastic variance-ratio test, direct (non-ACF-derived) formula:
   VR(q) = sigma_c^2(q) / sigma_a^2, z = (VR(q)-1) / sqrt(theta(q)),
   theta(q) = 2*(2q-1)*(q-1) / (3*q*N). NOTE (limitation, not fixed here): this is the
   HOMOSKEDASTIC variant -- financial returns are well known to show volatility clustering, which
   the homoskedastic null doesn't model, so its significance levels are indicative, not exact.
   The heteroskedasticity-robust Lo-MacKinlay variant is a real, flagged limitation, not built in
   this pass (would be a separately-scoped follow-up, not silently assumed unnecessary).
3. Wald-Wolfowitz runs test on the SIGN of non-overlapping q-bar returns at each horizon (must be
   non-overlapping for the runs test's independence assumption to hold even under a true random
   walk -- overlapping q-bar sums are mechanically autocorrelated by construction regardless of
   any real market structure).

Three windows, all previously established (Experiment 1/2's own boundaries), not re-chosen here:
TRAIN (2026-05-30 -> 2026-07-22 22:46), HELD-OUT (2026-07-22 22:47 -> 2026-08-05 08:00), LIVE
(2026-08-05 08:01 -> 2026-08-13 10:35).

Multiple-comparisons handling: 3 windows x 5 horizons x 3 test families = up to 45 individual
p-values. Raw (uncorrected) significance is reported per cell, but the headline interpretation
requires BOTH Bonferroni-corrected significance (alpha/45 approx 0.00111) AND consistency across
all three windows before anything is treated as real structure -- a single significant lag in one
window, at the uncorrected 0.05 level, is explicitly not treated as evidence (per the approved
plan's own instruction).

FULLY OFFLINE -- no MCP/MT5 call of any kind (not even the usual one-off get_symbol_info; this
script only reads the already-cached var/market_data/BTCUSD_M1.csv file). No production default
touched, no strategy/signal built or tuned -- this is pure statistical analysis of the raw price
series, not a backtest.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"
SYMBOL = "BTCUSD"
TIMEFRAME = "M1"

TRAIN_END = datetime(2026, 7, 22, 22, 47, 0, tzinfo=timezone.utc)
TEST_END = datetime(2026, 8, 5, 8, 0, 0, tzinfo=timezone.utc)

HORIZONS = [1, 5, 15, 30, 60]  # bars, pre-specified, fixed before running
NUM_TESTS_TOTAL = 3 * len(HORIZONS) * 3  # windows x horizons x {ACF, VR, runs}
ALPHA = 0.05
BONFERRONI_ALPHA = ALPHA / NUM_TESTS_TOTAL


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
    z = coef / (1 / math.sqrt(n))  # approx z under white-noise null, se=1/sqrt(n)
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


def main() -> None:
    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    all_bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not all_bars:
        raise RuntimeError(f"No cached bars at {path}")

    train_bars = [b for b in all_bars if b.time < TRAIN_END]
    test_bars = [b for b in all_bars if TRAIN_END <= b.time <= TEST_END]
    live_bars = [b for b in all_bars if b.time > TEST_END]
    windows = {"TRAIN": train_bars, "HELD-OUT": test_bars, "LIVE": live_bars}

    print(f"Multiple-comparisons context: {NUM_TESTS_TOTAL} total individual tests "
          f"(3 windows x {len(HORIZONS)} horizons x 3 test families). "
          f"Uncorrected alpha={ALPHA}, Bonferroni-corrected alpha={BONFERRONI_ALPHA:.6f}.\n")

    all_results: dict[str, dict] = {}

    for window_label, bars in windows.items():
        closes = [b.close for b in bars]
        log_prices = [math.log(c) for c in closes]
        returns = [log_prices[i] - log_prices[i - 1] for i in range(1, len(log_prices))]
        n = len(returns)
        print(f"\n================ WINDOW: {window_label} "
              f"({len(bars)} bars, {n} returns, {bars[0].time} -> {bars[-1].time}) ================")

        window_results = {"acf": {}, "vr": {}, "runs": {}}

        print("\n--- ACF (1-bar log returns) ---")
        for lag in HORIZONS:
            coef, band, p = _acf(returns, lag)
            sig_raw = abs(coef) > band
            sig_bonf = p < BONFERRONI_ALPHA
            print(f"  lag={lag:>3}: ACF={coef:+.5f}  band=+/-{band:.5f}  p={p:.4f}  "
                  f"sig(raw 0.05)={sig_raw}  sig(bonferroni)={sig_bonf}")
            window_results["acf"][lag] = (coef, p, sig_raw, sig_bonf)

        print("\n--- Lo-MacKinlay variance ratio (homoskedastic) ---")
        for q in HORIZONS:
            vr, z, p = _variance_ratio(log_prices, q)
            sig_raw = p < ALPHA
            sig_bonf = p < BONFERRONI_ALPHA
            direction = "momentum-like (VR>1)" if vr > 1 else ("mean-reversion-like (VR<1)" if vr < 1 else "neutral")
            print(f"  q={q:>3}: VR={vr:.4f}  z={z:+.2f}  p={p:.4f}  {direction}  "
                  f"sig(raw 0.05)={sig_raw}  sig(bonferroni)={sig_bonf}")
            window_results["vr"][q] = (vr, p, sig_raw, sig_bonf)

        print("\n--- Runs test (sign of non-overlapping q-bar returns) ---")
        for q in HORIZONS:
            runs, n1, n2, z, p = _runs_test(log_prices, q)
            sig_raw = p < ALPHA
            sig_bonf = p < BONFERRONI_ALPHA
            direction = "fewer runs than random (momentum-like)" if z < 0 else "more runs than random (mean-reversion-like)"
            print(f"  q={q:>3}: runs={runs} (n_up={n1}, n_down={n2})  z={z:+.2f}  p={p:.4f}  "
                  f"{direction if sig_raw else 'no departure'}  "
                  f"sig(raw 0.05)={sig_raw}  sig(bonferroni)={sig_bonf}")
            window_results["runs"][q] = (z, p, sig_raw, sig_bonf)

        all_results[window_label] = window_results

    print("\n\n=== CROSS-WINDOW CONSISTENCY SUMMARY ===")
    print("(a cell is only flagged CONSISTENT if the SAME sign/direction is raw-significant in "
          "all 3 windows at the same horizon -- the bar this experiment's plan requires before "
          "treating anything as real structure)\n")

    # For VR, "direction" is VR-1 (>0 => momentum-like, <0 => mean-reversion-like) since VR
    # itself is always positive by construction -- the raw VR value's own sign is meaningless.
    centering = {"acf": lambda v: v, "vr": lambda v: v - 1.0, "runs": lambda v: v}

    for family, values_key in [("ACF", "acf"), ("Variance Ratio", "vr"), ("Runs test", "runs")]:
        print(f"--- {family} ---")
        center = centering[values_key]
        for h in HORIZONS:
            cells = []
            for w in windows:
                entry = all_results[w][values_key][h]
                coef_or_stat = center(entry[0])
                sig_raw = entry[2]
                sig_bonf = entry[3]
                sign = "+" if coef_or_stat > 0 else "-"
                flag = "**" if sig_bonf else ("*" if sig_raw else "")
                cells.append(f"{w}:{sign}{flag}")
            same_sign = len({("+" if center(all_results[w][values_key][h][0]) > 0 else "-") for w in windows}) == 1
            all_raw_sig = all(all_results[w][values_key][h][2] for w in windows)
            all_bonf_sig = all(all_results[w][values_key][h][3] for w in windows)
            consistent = same_sign and all_raw_sig
            print(f"  horizon={h:>3}: {'  '.join(cells)}   "
                  f"same_sign_all_windows={same_sign}  raw_sig_all_windows={all_raw_sig}  "
                  f"CONSISTENT={consistent}  bonferroni_sig_all_windows={all_bonf_sig}")
        print()

    print("=====================================================================\n")


if __name__ == "__main__":
    main()
