#!/usr/bin/env python3
"""
Session/time-of-day seasonality research (approved 2026-08-14,
docs/SESSION_SEASONALITY_RESEARCH_CHECKPOINT.md): a new, separately-scoped research thread, not a
continuation of the closed `docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md` or
`docs/XAUUSD_SIGNAL_EDGE_CHECKPOINT.md` investigations. Those tested serial dependence of returns
(does the past predict the future?) and both closed negative. This tests a genuinely different
mechanism -- session/calendar structure (does *when* a bar occurs predict its return/volatility
characteristics?) -- and doubles as a diagnostic for a hypothesis Experiment 3 raised but never
resolved: is BTCUSD M1's confirmed 1-bar runs-test effect microstructure noise (session-
concentrated, e.g. thin Asian-session order books) or something more genuine (uniform or
concentrated in high-liquidity sessions instead)?

SESSION BOUNDARIES (UTC, pre-registered, fixed before any test ran) -- see the checkpoint doc's
own correction note: an earlier draft counted London/NY-overlap bars in two groups, which would
have violated the independence assumption both tests below require. Corrected to three
EXHAUSTIVE, NON-OVERLAPPING 8-hour buckets: Asian 00:00-08:00, London 08:00-16:00,
New York 16:00-24:00. A documented realism/validity tradeoff (the true NY session opens ~13:00),
not an oversight.

WEEKDAY BUCKETS: Monday-Friday for the weekday test; weekend bars (BTCUSD trades 24/7 and will
have them, XAUUSD's demo broker may not) are reported separately, never dropped or merged into an
adjacent weekday.

TESTS (zero free parameters beyond the boundaries above, off-the-shelf, pure Python -- this
project's dependency-free convention, see features/atr.py's own docstring for precedent):
1-2. Kruskal-Wallis (non-parametric, robust to non-normal financial return distributions) on mean
     1-bar log return, and on volatility (|log return|), grouped by session bucket (3 groups,
     df=2).
3-4. Same two tests grouped by weekday bucket (5 groups, df=4).
5.   Diagnostic: chi-square test of independence on a 3-session x 2-outcome ("did this bar's
     return reverse sign from the immediately preceding bar?") contingency table, using only real,
     temporally-adjacent consecutive pairs -- see the checkpoint doc's own correction note for why
     a session-filtered SUBSEQUENCE re-run of the runs test would have silently changed what it
     measures (day-over-day persistence instead of intra-session reversal).

Both Kruskal-Wallis (df=2 and df=4) and the chi-square independence test (df=2) use exact
closed-form chi-square survival functions -- valid because all three df values here are even, so
no numerical integration or external dependency (scipy) is needed, consistent with this project's
existing pure-Python statistical helpers (Experiments 3/4's own ACF/variance-ratio/runs-test code).

WINDOWS: the same chronological-thirds convention (EARLY/MIDDLE/RECENT) already established by
Experiment 4, reused rather than reinvented. SCOPE: M1 only for both BTCUSD and XAUUSD (the
timeframe carrying the already-confirmed 1-bar effect this thread's diagnostic targets) --
M15/H1 escalation only if this finds something worth chasing, mirroring Experiments 3->4's own
escalate-only-if-warranted discipline.

FULLY OFFLINE -- reads only the M1 caches already seeded for both symbols (BTCUSD from the
original investigation, XAUUSD from its own Step 1). No MCP call of any kind in this script. No
production default touched, no strategy built or tuned.
"""

from __future__ import annotations

import math
from pathlib import Path

from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"
SYMBOLS = ["BTCUSD", "XAUUSD"]
TIMEFRAME = "M1"

SESSION_BOUNDS = [("ASIAN", 0, 8), ("LONDON", 8, 16), ("NY", 16, 24)]
WEEKDAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI"]  # weekday() 0-4; 5/6 = weekend, reported separately
ALPHA = 0.05
NUM_TESTS_TOTAL = len(SYMBOLS) * 3 * 5  # 2 symbols x 3 windows x 5 tests (1-4 + diagnostic)
BONFERRONI_ALPHA = ALPHA / NUM_TESTS_TOTAL


def _session_bucket(hour: int) -> str:
    for name, start, end in SESSION_BOUNDS:
        if start <= hour < end:
            return name
    raise AssertionError(f"hour={hour} matched no session bucket")


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _two_sided_p(z: float) -> float:
    return 2 * (1 - _normal_cdf(abs(z)))


def _chi2_sf_even_df(x: float, df: int) -> float:
    """Exact chi-square survival function (upper-tail p-value) for even df, via the closed-form
    Erlang-distribution relationship -- valid for every df used in this script (2 and 4), no
    external dependency needed. Returns 1.0 for x<=0 (all mass is at/above 0)."""
    if df < 2 or df % 2 != 0:
        raise ValueError(f"df must be a positive even integer, got {df}")
    if x <= 0:
        return 1.0
    k = df // 2
    total = 0.0
    term = 1.0  # (x/2)^0 / 0!
    total += term
    for i in range(1, k):
        term *= (x / 2) / i
        total += term
    return math.exp(-x / 2) * total


def _rank_with_ties(values: list[float]) -> list[float]:
    """1-indexed average rank per value, ties get the average rank of their tied block."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for m in range(i, j + 1):
            ranks[order[m]] = avg_rank
        i = j + 1
    return ranks


def _kruskal_wallis(groups: list[list[float]]) -> tuple[float, float, int]:
    """Returns (H, p, df). Tie-corrected, exact chi-square p-value (df=len(groups)-1, must be
    even for the callers in this script -- 3 session groups -> df=2, 5 weekday groups -> df=4)."""
    df = len(groups) - 1
    all_values: list[float] = []
    sizes = []
    for g in groups:
        all_values.extend(g)
        sizes.append(len(g))
    n = len(all_values)
    ranks = _rank_with_ties(all_values)

    sorted_vals = sorted(all_values)
    tie_sum = 0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        t = j - i + 1
        if t > 1:
            tie_sum += t ** 3 - t
        i = j + 1

    idx = 0
    rank_sums = []
    for size in sizes:
        rank_sums.append(sum(ranks[idx: idx + size]))
        idx += size

    h_raw = (12.0 / (n * (n + 1))) * sum(rs ** 2 / gs for rs, gs in zip(rank_sums, sizes)) - 3 * (n + 1)
    tie_correction = 1 - tie_sum / (n ** 3 - n) if n > 1 else 1.0
    h = h_raw / tie_correction if tie_correction > 0 else h_raw
    h = max(h, 0.0)
    p = _chi2_sf_even_df(h, df)
    return h, p, df


def _chi_square_independence_2col(table: dict[str, tuple[int, int]]) -> tuple[float, float, int]:
    """`table`: {row_label: (count_outcome_true, count_outcome_false)}. Returns (chi2, p, df)."""
    row_totals = {r: sum(v) for r, v in table.items()}
    col_totals = (sum(v[0] for v in table.values()), sum(v[1] for v in table.values()))
    grand_total = sum(row_totals.values())
    chi2 = 0.0
    for r, (obs_true, obs_false) in table.items():
        for obs, col_total in zip((obs_true, obs_false), col_totals):
            expected = row_totals[r] * col_total / grand_total
            if expected > 0:
                chi2 += (obs - expected) ** 2 / expected
    df = (len(table) - 1) * (2 - 1)
    p = _chi2_sf_even_df(chi2, df) if df >= 2 and df % 2 == 0 else float("nan")
    return chi2, p, df


def _chronological_thirds(bars):
    n = len(bars)
    a, b = n // 3, 2 * n // 3
    return {"EARLY": bars[:a], "MIDDLE": bars[a:b], "RECENT": bars[b:]}


def _analyze_window(window_label: str, bars) -> None:
    closes = [b.close for b in bars]
    log_prices = [math.log(c) for c in closes]
    returns = [log_prices[i] - log_prices[i - 1] for i in range(1, len(log_prices))]
    return_bars = bars[1:]  # bars[i] pairs with returns[i-1] -- returns[k] belongs to bar (k+1)

    print(f"\n================ WINDOW: {window_label} "
          f"({len(bars)} bars, {len(returns)} returns, {bars[0].time} -> {bars[-1].time}) ================")

    session_returns: dict[str, list[float]] = {name: [] for name, _, _ in SESSION_BOUNDS}
    session_vol: dict[str, list[float]] = {name: [] for name, _, _ in SESSION_BOUNDS}
    weekday_returns: dict[str, list[float]] = {d: [] for d in WEEKDAY_NAMES}
    weekday_vol: dict[str, list[float]] = {d: [] for d in WEEKDAY_NAMES}
    weekend_count = 0

    for ret, bar in zip(returns, return_bars):
        session = _session_bucket(bar.time.hour)
        session_returns[session].append(ret)
        session_vol[session].append(abs(ret))
        wd = bar.time.weekday()
        if wd < 5:
            weekday_returns[WEEKDAY_NAMES[wd]].append(ret)
            weekday_vol[WEEKDAY_NAMES[wd]].append(abs(ret))
        else:
            weekend_count += 1

    print("\n--- Session bucket sizes ---")
    for name, _, _ in SESSION_BOUNDS:
        print(f"  {name:>7}: {len(session_returns[name])} returns")

    print("\n--- Weekday bucket sizes ---")
    for d in WEEKDAY_NAMES:
        print(f"  {d}: {len(weekday_returns[d])} returns")
    print(f"  WEEKEND (excluded from weekday test): {weekend_count} returns")

    session_groups = [session_returns[name] for name, _, _ in SESSION_BOUNDS]
    h, p, df = _kruskal_wallis(session_groups)
    sig = p < BONFERRONI_ALPHA
    print(f"\n--- Test 1: Kruskal-Wallis, mean return by SESSION ---")
    print(f"  H={h:.4f}  df={df}  p={p:.6f}  sig(bonferroni)={sig}")

    session_vol_groups = [session_vol[name] for name, _, _ in SESSION_BOUNDS]
    h, p, df = _kruskal_wallis(session_vol_groups)
    sig = p < BONFERRONI_ALPHA
    print(f"--- Test 2: Kruskal-Wallis, volatility (|return|) by SESSION ---")
    print(f"  H={h:.4f}  df={df}  p={p:.6f}  sig(bonferroni)={sig}")

    weekday_groups = [weekday_returns[d] for d in WEEKDAY_NAMES]
    h, p, df = _kruskal_wallis(weekday_groups)
    sig = p < BONFERRONI_ALPHA
    print(f"--- Test 3: Kruskal-Wallis, mean return by WEEKDAY ---")
    print(f"  H={h:.4f}  df={df}  p={p:.6f}  sig(bonferroni)={sig}")

    weekday_vol_groups = [weekday_vol[d] for d in WEEKDAY_NAMES]
    h, p, df = _kruskal_wallis(weekday_vol_groups)
    sig = p < BONFERRONI_ALPHA
    print(f"--- Test 4: Kruskal-Wallis, volatility (|return|) by WEEKDAY ---")
    print(f"  H={h:.4f}  df={df}  p={p:.6f}  sig(bonferroni)={sig}")

    reversal_counts: dict[str, list[int]] = {name: [0, 0] for name, _, _ in SESSION_BOUNDS}
    for i in range(1, len(returns)):
        prev_ret, cur_ret = returns[i - 1], returns[i]
        if prev_ret == 0 or cur_ret == 0:
            continue
        session = _session_bucket(return_bars[i].time.hour)
        reversed_sign = (prev_ret > 0) != (cur_ret > 0)
        reversal_counts[session][0 if reversed_sign else 1] += 1

    table = {name: (counts[0], counts[1]) for name, counts in reversal_counts.items()}
    chi2, p, df = _chi_square_independence_2col(table)
    sig = p < BONFERRONI_ALPHA
    print(f"--- Test 5: chi-square independence, sign-reversal rate by SESSION ---")
    for name, (rev, no_rev) in table.items():
        total = rev + no_rev
        rate = rev / total if total > 0 else float("nan")
        print(f"  {name:>7}: reversed={rev:>6} not_reversed={no_rev:>6} rate={rate:.4f}")
    print(f"  chi2={chi2:.4f}  df={df}  p={p:.6f}  sig(bonferroni)={sig}")


def _analyze_symbol(symbol: str) -> None:
    path = cache_path(CACHE_DIR, symbol, TIMEFRAME)
    all_bars = load_bars(path, symbol, TIMEFRAME)
    if not all_bars:
        raise RuntimeError(f"No cached bars at {path}")

    print(f"\n\n#################### SYMBOL: {symbol} {TIMEFRAME} "
          f"({len(all_bars)} bars, {all_bars[0].time} -> {all_bars[-1].time}) ####################")

    windows = _chronological_thirds(all_bars)
    for label, bars in windows.items():
        _analyze_window(label, bars)


def main() -> None:
    print(f"Multiple-comparisons context: {NUM_TESTS_TOTAL} tests total "
          f"({len(SYMBOLS)} symbols x 3 windows x 5 tests). "
          f"Uncorrected alpha={ALPHA}, Bonferroni alpha={BONFERRONI_ALPHA:.6f}.")
    print(f"Session boundaries (UTC, non-overlapping): "
          + ", ".join(f"{name} {start:02d}:00-{end:02d}:00" for name, start, end in SESSION_BOUNDS))

    for symbol in SYMBOLS:
        _analyze_symbol(symbol)

    print("\nNo production parameter adopted by this script. No Step 7 run. No Live Pilot work.")
    print("\n=====================================================================\n")


if __name__ == "__main__":
    main()
