# Checkpoint: session/time-of-day seasonality research (new, separately-scoped effort) -- CLOSED 2026-08-14

**Overall recommendation: ABANDON as a directional-edge lead.** No session or weekday effect on
mean return was found for either symbol, in any window. Volatility does cluster by session/
weekday (a real, expected, cross-window-robust effect) but is not itself a directional signal.
The diagnostic test found the already-confirmed 1-bar reversal effect does not concentrate in the
illiquid Asian session, undermining that specific microstructure-noise explanation. See "Status:
CLOSED" below for full results and reasoning.

A new effort, not a continuation of `docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md` or
`docs/XAUUSD_SIGNAL_EDGE_CHECKPOINT.md` — both of those closed 2026-08-14 with the same negative
conclusion (no cost-surviving edge, BTCUSD or XAUUSD), and per
`docs/LIVE_PILOT_PREPARATION_CHECKPOINT.md`'s updated Stage D note, there is currently no open
candidate strategy/instrument combination with credible edge. Every closed experiment tested one
economic mechanism — **serial dependence of returns** (does return[t] predict return[t+k]? —
MACD-sign, ACF, variance ratio, runs test, fade-the-last-bar). This thread tests a **genuinely
different mechanism: session/calendar structure** (does mean return or volatility vary by time of
day or day of week?) — not a parameter variant or a mechanical retranslation of what's already
been tried.

**Not yet approved to execute — design/scoping only**, same standing discipline as every prior
experiment in both closed investigations.

## Why this is a different signal shape, not more of the same ladder

Momentum/mean-reversion tests ask "does the past few bars' direction predict the next bars'
direction." Session/seasonality tests ask an unrelated question: "does *when* a bar occurs (which
trading session, which day of week) predict its return or volatility characteristics." The
underlying real-world mechanism is liquidity/participation cycling (session opens/closes, weekday
vs. weekend), not price-action autocorrelation — a well-documented, a priori plausible phenomenon
in real FX/commodities markets (gold specifically has known London/NY-session volatility
patterns), not a fishing expedition invented to keep searching the same haystack.

**This thread is also directly diagnostic for an open question the closed investigations raised
but never resolved**: Experiment 3 (`docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md`)
flagged BTCUSD M1's confirmed 1-bar runs-test effect as likely "the signature of microstructure
noise (bid-ask bounce/quantization), not genuine predictability" — a hypothesis, not a proven
conclusion. If that 1-bar effect turns out to be concentrated specifically in low-liquidity
sessions (Asian session, thin order books), that corroborates the microstructure-noise
explanation directly. If it's uniform across sessions, or concentrated in *high*-liquidity
sessions instead, that would undercut the microstructure explanation and be a genuinely
noteworthy finding worth a closer look — either outcome is informative, not just "more searching."

## Proposed design (fixed before running, not tuned after seeing results)

**Data**: reuses the M1 caches already on disk for both BTCUSD and XAUUSD
(`var/market_data/BTCUSD_M1.csv`, `var/market_data/XAUUSD_M1.csv`, both already seeded) — **zero
new live calls needed**, not even for this thread's own Step 1. Scope starts at M1 (finest
granularity, most bars, and the timeframe carrying the already-confirmed 1-bar effect this
thread's diagnostic test targets); M15/H1 escalation only if M1 shows something worth chasing,
mirroring the established "escalate only if warranted" discipline from Experiments 3→4.

**Pre-registered session boundaries (UTC, fixed before any test runs) — CORRECTED before
implementation, 2026-08-14**: the original draft above counted London/NY-overlap bars in two
groups simultaneously (a common convention for descriptive session stats) — caught before writing
any code as a real problem for this specific design, not a stylistic nitpick: Kruskal-Wallis and
the chi-square independence test below both assume each observation belongs to exactly one group,
and double-counting overlap bars would spuriously correlate the London and NY groups' apparent
distributions. Corrected to three **exhaustive, non-overlapping** 8-hour buckets instead:
- Asian: 00:00–08:00
- London: 08:00–16:00
- New York: 16:00–24:00

This trades a small amount of session-boundary realism (the true NY session opens around 13:00,
inside what this scheme calls "London") for a statistically valid partition — an explicit,
documented tradeoff, not an oversight.

**Pre-registered day-of-week buckets**: Monday–Friday. Weekend bars (BTCUSD trades 24/7 and will
have them; XAUUSD may have thin/absent weekend activity depending on broker hours) reported
separately, not silently dropped or merged into an adjacent weekday.

**Tests (off-the-shelf, zero free parameters beyond the boundaries above)**:
1. Kruskal-Wallis test (non-parametric, robust to non-normal return distributions — appropriate
   for financial returns) on mean 1-bar return, grouped by session bucket.
2. Same test on realized volatility (|return| or bar range) by session bucket — this one is
   expected to find *something* (session-driven volatility clustering is well-established), so it
   doubles as a validity check that the methodology can detect a known real effect, distinguishing
   "the test works" from "there is nothing to find."
3. Same Kruskal-Wallis approach across weekday buckets, both metrics.
4. **Diagnostic** (also corrected before implementation, same reason as the session-boundary fix
   above): re-running the runs test on a session-filtered SUBSEQUENCE of bars would silently
   change what it measures — a "next bar" in a filtered subsequence is no longer 1 real minute
   later, it is whatever the next bar assigned to that same session happens to be, often ~16 hours
   away, testing day-over-day persistence instead of intra-session microstructure. Corrected
   design: for every real, temporally-adjacent consecutive pair of bars (i-1, i) in the full,
   ungapped sequence, compute a binary "reversal" indicator (sign(return[i]) != sign(return[i-1]),
   pairs where either return is exactly zero excluded, same tie convention the runs test itself
   already uses), label it by bar i's own session bucket, and run a chi-square test of
   independence on the resulting 3-session x 2-outcome contingency table. This asks the same
   question (is the excess-reversal effect concentrated in one session?) without ever breaking
   temporal adjacency.

**Windows**: the same chronological-thirds convention (EARLY/MIDDLE/RECENT) already established
for each symbol, reused rather than reinvented, so this thread's cross-window-consistency
requirement stays comparable to every prior experiment's.

**Multiple comparisons**: Bonferroni-corrected across the full pre-specified test count (2
symbols x [session test + weekday test] x [return + volatility] x 3 windows, plus the 3-session
diagnostic re-run of the runs test x 3 windows x 2 symbols) — exact count fixed and printed by the
script itself before any result is read, same convention as every prior experiment.

## Scope discipline (same commitments as both closed investigations)

Fully offline — no MCP call of any kind anticipated even at Step 1, since both symbols' M1 caches
already exist. No Step 7. No Live Pilot order of any kind. No production
`RunnerStrategyConfig`/`GridStrategyConfig` default changed. No parameter search beyond the
pre-specified, fixed session/weekday boundaries and horizon set above — none to be expanded after
seeing results. If this thread finds cross-window-consistent structure, translating it into a
tradeable rule (a session-filtered variant of an existing signal, most likely) would be its own
separately-scoped, separately-approved step — mirroring Experiment 5's own discipline of never
assuming statistical structure is an economic edge until explicitly tested.

## Status: CLOSED 2026-08-14

**Approved and run same day.** Script:
`scripts/run_demo_execution_check_20260814_session_seasonality_test.py`. All statistical helpers
(`_chi2_sf_even_df`, `_kruskal_wallis`, `_chi_square_independence_2col`) smoke-tested against
known reference values before running on real data (chi-square critical values at df=2/df=4
matched to 4 decimal places; a textbook Kruskal-Wallis example reproduced exactly; a skewed
contingency table produced the expected small p-value) -- see this session's transcript. 30 tests
total (2 symbols x 3 windows x 5 tests), Bonferroni alpha=0.001667. Full output:
`var/session_seasonality_output.log`.

**Results**:

| Test | Finding |
|---|---|
| 1: mean return by session | Never significant -- no session, either symbol, any window |
| 2: volatility by session | Significant in 5/6 cells (BTCUSD all 3 windows; XAUUSD EARLY+RECENT, not MIDDLE) |
| 3: mean return by weekday | Never significant -- no session, either symbol, any window |
| 4: volatility by weekday | Significant in all 6/6 cells -- strong, robust, cross-window |
| 5: reversal-rate diagnostic (by session) | Never significant -- reversal rate essentially uniform across sessions |

**Synthesis and recommendation: no new tradeable signal found; ABANDON as a directional-edge
lead.** Mean return never varies by session or weekday, for either symbol, in any window (Tests
1 and 3) -- rules out "trade a specific session/day" as a directional edge candidate. Volatility
genuinely clusters by session and (especially) weekday (Tests 2 and 4, cross-window-robust) -- a
real, expected phenomenon that confirms the methodology can detect genuine effects (not simply
underpowered), but volatility clustering by itself is not a directional trading signal; turning
it into one (risk-timing rather than direction-picking) would be a materially different strategy
shape and its own separately-scoped question, not a direct continuation of this thread.

**Diagnostic result (Test 5)**: the already-confirmed 1-bar reversal effect from Experiment 3/4
does NOT concentrate in the illiquid Asian session -- reversal rates are close to uniform across
all three sessions in every window, for both symbols. This undermines the specific "thin-Asian-
liquidity bid-ask-bounce" version of the microstructure-noise hypothesis (Experiment 3's own
flagged-but-unresolved explanation), though it does not fully resolve what mechanism *does*
produce the effect -- a genuinely informative negative result, not an inconclusive one.

**Where this leaves the broader picture**: three research threads have now closed with the same
directional-edge answer (BTCUSD, XAUUSD, and now session/calendar seasonality on both) -- none
found a currently-exploitable directional edge with this project's methodologies. `pytest -q` ->
556 passed, unaffected (no `src/` file touched, one new script + this doc only).
