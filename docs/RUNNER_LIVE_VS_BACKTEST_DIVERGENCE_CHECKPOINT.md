# Checkpoint: Runner live-vs-backtest divergence investigation (Phase 8 continuation)

A new, separately-scoped effort motivated by a finding surfaced while reviewing Phase 9 Stage A/B
closure (2026-08-13 afternoon session) — same pattern as the grid regime filter effort after
Phase 8 Step 7 (`docs/GRID_REGIME_FILTER_CHECKPOINT.md`): a follow-on investigation into a
specific finding, not a re-opening of Phase 8 itself. Per `AGENTS.md`'s explicit direction from
that session: separate "execution framework readiness" (Phase 9 Stage A/B/C, essentially closed)
from "strategy profitability readiness" (still negative), and prioritize the latter before any
further Stage D / Live Pilot investment. **Research only throughout — no Step 7 run, no Live
Pilot work, no live/demo order of any kind, no production parameter changed.**

## Trigger: a live-performance re-read revealed runner's sample was incomplete, then a param mix

Re-running `scripts/run_demo_execution_live_performance_monitor.py` (read-only) after the same
session's StateStore backlog reconciliation (Stage A item 4) found grid/runner trade counts had
jumped from the morning's 90/28 to 169/64 — 115 previously-stale `OPEN` records had just become
joinable, not new trading activity. Readiness checklist rows 3–4 closed to fully MET for both
strategies on this corrected read (grid: 169 trades, −0.683 R, 115.505 R drawdown, 32.0% win
rate, 0.154 profit factor; runner: 64 trades, −0.607 R, 41.561 R drawdown, 20.3% win rate, 0.361
profit factor) — see `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md`.

Splitting runner's 64 trades by which code was live when each was submitted
(`scripts/run_demo_execution_check_20260813_runner_param_split.py`) found the sample blended
three structurally different configs, not one:

| Group | Config | Trades | Expectancy | Win rate | Profit factor | Avg SL distance |
|---|---|---|---|---|---|---|
| A | `sl_atr_mult=1.5`, no floor (pre-2026-08-05) | 30 | −0.333 R | 26.7% | 0.604 | 0.058% of price |
| B | `sl_atr_mult=3.0`, no floor (2026-08-05 → 2026-08-11T04:42Z, the retcode-10016 bug window) | 32 | −0.898 R | 12.5% | 0.189 | 0.094% of price |
| C | `sl_atr_mult=3.0`, WITH the 1% floor (2026-08-11T04:42Z onward — today's actual deployed config) | 2 | −0.067 R | 50.0% | 0.109 | 3.001% of price |

Only 2 of 64 live trades reflect the strategy as currently deployed — the −0.100 R backtest
figure that justified adopting `sl_atr_mult=3.0` (Phase 8 Step 6) predates the SL-floor fix
(`f41a5a1`, 2026-08-11) by 6 days, so it was never a valid comparator for today's actual config.

**Re-running Step 6's own validation script unmodified, with today's code** confirmed this
directly: **zero closed runner trades**, both configs tested, across the entire 19,000-bar
held-out window. Log evidence: all 7,560 runner rejection lines read `symbol.position_limit` — a
single position opened almost immediately and then blocked every later cycle for the rest of the
13-day window because it never resolved at the new, much wider (~3%/6% of price) stop distance.
**The currently-deployed runner configuration had never been backtested at all.**

Re-seeding the market data cache through today (`scripts/run_demo_execution_historical_data_cache_seed.py`,
one read-only live pull, cache now 2026-05-30 → 2026-08-13) and re-running the backtest over the
*actual* live-trading window (`scripts/run_demo_execution_check_20260813_live_window_backtest.py`)
reproduced the same zero-trade result window-independently — ruling out "wrong window" as the
explanation and pointing at the floor+concurrency interaction as a design-level mechanism, not a
regime artifact. The same script's no-floor/same-window run (446 trades, −0.260 R, 24.7% win
rate) gave the first genuinely apples-to-apples (same window, same config) comparison against
live Group B (−0.898 R) — a real gap survived even with window and config both controlled for,
motivating Experiment 2 below.

## Experiment 1 (approved 2026-08-13): does `max_concurrent_positions=1` explain the gap?

Full design and results: this session's transcript; script:
`scripts/run_demo_execution_check_20260813_experiment1_concurrency.py`. Pre-specified concurrency
set `{1, 2, 3, 5}` (fixed before running), both floor and no-floor variants, TRAIN
(2026-05-30→2026-07-22, 76,000 bars) and HELD-OUT (2026-07-22→2026-08-05, 19,000 bars) — the
exact Phase 8 Step 5/6 boundaries, not re-split.

- Cap=1 confirmed as a severe **opportunity-cost** bottleneck: 99.7–100% of directional signals
  blocked under the floor config, 86.9%/85.4% under no-floor.
- But relaxing it does **not** recover better per-trade quality on the large, reliable no-floor
  sample (1,989–8,486 trades): expectancy stays flat in a narrow −0.065 to −0.102 R band across
  every cap level, win rate flat ~30–31%, profit factor flat ~0.85–0.91 — while drawdown scales
  almost linearly with cap (172R → 617R on TRAIN). More concurrency admits more of the *same*
  losing-quality trades, not previously-missed good ones.
- The floor config's apparent improvement with higher cap (TRAIN: −0.167 R at cap=1 → −0.012 R
  at cap=5) is based on too thin a sample (18–85 trades, near-zero on HELD-OUT) to trust, and
  contradicts the larger no-floor pattern — flagged as likely noise, not adopted as a finding.
- A second guard (`ExposureCaps.max_open_lots=0.06` ÷ `0.01` lot = a hard 6-position ceiling)
  binds independently of `max_concurrent_positions` once cap≥5 — `peak_concurrent=6` regardless
  of variant at cap=5, confirmed by log inspection.

**Classification: B — concurrency contributes (confirmed opportunity-cost bottleneck) but does
not explain the lack of edge (relaxing it doesn't improve expectancy).**

## Experiment 2 (approved 2026-08-13): does the runner signal carry genuine edge?

Full design and results: this session's transcript; script:
`scripts/run_demo_execution_check_20260813_experiment2_edge_test.py`. Fixed config (no-floor,
`max_concurrent_positions=1`, `fast=12`/`slow=26` untouched — no parameter search). Real
MACD-sign signal vs. a same-cost/same-risk random-direction baseline (20 pre-registered seeds,
runtime-monkeypatched, nothing on disk changed), across three windows: TRAIN, HELD-OUT, and LIVE
(2026-08-05→2026-08-13, the actual live-trading period, newly available after the cache reseed).

| Window | Real expectancy | Random mean | Delta | z-score | Real outside random range? |
|---|---|---|---|---|---|
| TRAIN | −0.065 R | −0.049 R | −0.016 R | −0.62 | No |
| HELD-OUT | −0.100 R | −0.089 R | −0.011 R | −0.20 | No |
| LIVE | −0.227 R | −0.166 R | −0.061 R | −1.16 | No |

Real expectancy never once beat the random baseline's mean, in any window — always negative
delta, widening in the most recent (LIVE) window — but no gap reached statistical significance
(max |z| = 1.16; real always inside the random empirical range).

**Math check**: a 2:1 reward:risk bracket has an exact breakeven win rate of 1/3 (33.3%) —
not a rule of thumb but the gambler's-ruin result for a symmetric, driftless process (P(hit +2R
before −1R) = 1/(1+2) = 1/3). A perfectly informationless signal should net to ~zero before
costs at this bracket; the random baseline's negative means (−0.049 to −0.166 R) reflect real
spread costs pushing a fair game negative, not a biased bracket. Observed win rates (real:
31.2%/30.0%/25.8%; random: ~31.7%/30.4%/27.8%) sit at or below that 33.3% line in every window
for both real and random — the current 2:1 structure is not itself the problem; MACD-sign simply
doesn't push win rate above the bar it would need to clear.

**Classification: C — No edge.** Real is statistically indistinguishable from random-direction
chance in all three independent-ish windows.

## Experiment 3 (approved 2026-08-13): is BTCUSD M1 price action distinguishable from a random walk?

Full design and results: this session's transcript; script:
`scripts/run_demo_execution_check_20260813_experiment3_randomwalk.py`. Directly tests Experiment
2's "or is this just a random walk" branch instead of searching for alternative signals —
carries zero parameter-search/overfitting risk (standard, off-the-shelf tests, no free
parameters). Three tests (ACF, Lo–MacKinlay homoskedastic variance ratio, Wald–Wolfowitz runs
test on non-overlapping q-bar return signs), five pre-specified fixed horizons `{1, 5, 15, 30,
60}` bars (spanning runner's own observed holding times), same three windows as Experiments 1–2.
Fully offline — no MCP call of any kind, not even the usual `get_symbol_info` (reads only the
already-cached CSV). 45 total individual tests; Bonferroni-corrected α = 0.00111 applied
alongside a pre-committed cross-window-consistency requirement (same sign, significant in all
three windows) before anything counts as real structure.

- **The only cross-window-consistent, Bonferroni-significant finding anywhere in the sweep is the
  1-bar runs test** (fewer runs than random in all three windows, z = −3.92 to −6.29). It's
  economically negligible (implied R² ≈ 0.002–0.23% of variance from the matching ACF magnitude)
  and it **contradicts** the 1-bar ACF's own sign in the LIVE window (ACF says mean-reversion,
  runs test says momentum, same window, same horizon) — the signature of microstructure noise
  (bid-ask bounce/quantization), not genuine predictability, and far below any horizon runner
  actually trades on.
- **At the horizons that matter (5–60 bars), the evidence is directionally inconsistent across
  windows.** LIVE shows a strong, Bonferroni-significant mean-reversion signal via variance ratio
  at every horizon (VR 0.72–0.91, all p<0.001) — but TRAIN shows no departure at all (VR≈1.00)
  and HELD-OUT leans the *opposite* direction (momentum-like, VR 1.04–1.10, only raw-significant).
  Nothing at these horizons clears the pre-committed consistency bar.

**Classification: C — Mixed/unstable structure.** Not D (there is a real, robust 1-bar effect);
not A/B (nothing at a trading-relevant horizon replicates across all three windows with the same
sign). Limitation flagged, not resolved: the homoskedastic variance-ratio null doesn't model
known volatility clustering, so its p-values are indicative, not exact; the three windows are
temporally sequential slices of one price history, not independent markets, so LIVE's isolated
mean-reversion signal could reflect either noise or a genuine recent regime shift — this
experiment can't distinguish the two.

## Experiment 4 (approved 2026-08-13): does BTCUSD show more reliable structure at M15/H1 than M1?

Full design and results: this session's transcript; scripts:
`scripts/run_demo_execution_check_20260813_experiment4_cache_seed.py` (new read-only caches:
M15 60,000 bars, 2024-11-10→2026-08-13, ~641 days; H1 60,000 bars, 2015-07-28→2026-08-13, ~11
years) and `scripts/run_demo_execution_check_20260813_experiment4_randomwalk_m15_h1.py` (Experiment
3's identical methodology, reused verbatim — same ACF/Lo-MacKinlay/runs-test formulas, same
`{1,5,15,30,60}` bar-count horizon set). Two approved, explicit departures from Experiment 3: (1)
windows are three roughly-equal **chronological thirds** of each timeframe's cached history
(EARLY/MIDDLE/RECENT) rather than Experiment 3's exact calendar boundaries, which would have left
M15/H1 badly underpowered (Experiment 3's spans collapse to ~800–5,000 bars at these coarser
timeframes); none is labeled "LIVE" since neither timeframe has ever been live-traded. (2) M15/H1
corrected separately (45 tests each), not pooled.

**Headline finding — the first result in this whole investigation to clear the full pre-committed
bar (same sign, significant, AND Bonferroni-robust, across all three independent windows):**
- **M15**: the 1-bar (15-minute) runs test is fully confirmed — more sign-reversals than random,
  same direction, Bonferroni-significant in all three ~20-month windows (Nov 2024→Aug 2026).
  Corroborated (not contradicted, unlike M1) by the ACF at the same horizon: 2 of 3 windows
  individually significant in the same direction, the third non-significant but not opposite.
  Nothing confirmed beyond 75 minutes.
- **H1**: the same pattern at the 1-bar (1-hour) horizon — fully confirmed, Bonferroni-significant
  in all three ~2-year windows spanning 2015→2026 (i.e., across meaningfully different BTC market
  eras, not just adjacent similar periods). A weaker version extends to 5 hours (same direction,
  raw-significant in all three, only 2/3 Bonferroni-robust). Nothing confirmed beyond that.
- Secondary, below-bar observation: M15's variance ratio shows the *same sign*
  (mean-reversion-like) in all 15/15 window×horizon cells, just not always significant — more
  directionally coherent than anything M1 produced even where it falls short of confirmation.

**Classification: M15 — B (Consistent mean-reversion structure, at the 1-bar horizon). H1 — B
(Consistent mean-reversion structure, at the 1-bar horizon, weaker support to 5 hours).** Both
contrast with M1's Experiment 3 result (C — mixed/unstable).

Limitations carried forward: same homoskedastic-variance-ratio caveat as Experiment 3; windows
are chronological thirds of one continuous history each, not independent markets (though H1's
spans genuinely different market-maturity eras); confirms serial structure exists, not that it
survives real transaction costs as a tradeable edge — that is Experiment 5's job, not assumed
here.

## Overall recommendation, updated: REDESIGN, deprioritize BTCUSD M1 directional, pursue M15/H1 mean-reversion as the next candidate

Three lines of evidence now converge on the M1-specific conclusion: Experiment 2 (MACD-sign has
no edge), Experiment 3 (M1 price action itself has no reliable directional structure at runner's
horizons). Experiment 4 adds a genuinely new, more promising direction: M15 and H1 both show a
real, confirmed, cross-era-robust short-horizon mean-reversion signature that M1 never showed.
KEEP (the current runner) remains ruled out. Full ABANDON remains too broad — the
execution/risk-management shape is sound and reusable; the signal-search premise on BTCUSD-M1
specifically is what's now deprioritized, not directional trading in general.

## Experiment 5 (approved 2026-08-13 as a scoping exercise, NOT YET RUN): does M15/H1 mean-reversion survive real costs as an economic edge?

**Design only, no execution this entry** — per explicit instruction to scope, not build/tune a
strategy. Full design: this session's transcript. Two candidates, tested independently, not
combined: **A. M15 single-bar mean-reversion**, **B. H1 single-bar mean-reversion**.

- **Signal** (zero free parameters): "fade the last closed bar" — SHORT if last bar's return was
  positive, LONG if negative. The most mechanically direct possible translation of Experiment 4's
  actual finding (the runs test measures exactly this relationship).
- **Exit — the one real, explicitly-flagged design decision awaiting approval before any code is
  written**: a fixed 1-bar holding period (enter at bar close, force-close at next bar's close),
  not runner's existing ATR-based bracket (built for a longer-hold momentum signal, would bury
  the confirmed 1-bar effect). Requires a small, additive, **backtest-harness-only** capability
  (a wrapper around the existing engine, not a change to `backtest/engine.py`/`pipeline/`/
  `strategy/`/`execution/`) plus a nominal ATR-based SL/TP sized to essentially never trigger
  inside 1 bar, purely to satisfy the existing MARKET-order-must-have-SL/TP safety rule and to
  give `r_multiple` a well-defined denominator.
- **Baselines**: random-direction (20 pre-registered seeds, same shape as Experiment 2),
  always-long, always-short.
- **Windows**: reuses Experiment 4's exact EARLY/MIDDLE/RECENT chronological thirds per
  timeframe — no new windows invented.
- **Costs**: same spread-only model as every prior experiment; each run done twice
  (`spread_multiplier=0` and `=1`) so cost drag is measured directly, not estimated.
- **Predefined classification**: A (robust economic edge, consistent across all 3 windows, beats
  both baseline families, survives cost) / B (weak/inconclusive) / C (structure exists at
  zero-cost, real-cost erases it) / D (no usable edge either way). A single good window cannot
  promote a candidate to A.
- **M15 vs. H1 comparison lens** (pre-committed, not yet filled in): sample span/regime diversity
  (H1 spans ~11 years incl. very different BTC eras; M15 spans ~641 days), execution practicality
  (H1's 1-hour cadence fits the existing bounded pipeline-loop shape far more naturally than M15's
  tighter, higher-turnover requirement), overfitting risk (H1's longer, more regime-diverse
  history is stronger out-of-sample evidence).

Not yet approved to execute — the fixed-1-bar-hold-plus-nominal-SL/TP mechanism needs explicit
sign-off before any harness code is written or any backtest is run.

**Execution approved 2026-08-14.** Script:
`scripts/run_demo_execution_check_20260813_experiment5_cost_edge_test.py`. Built exactly as
scoped above — a harness-only wrapper driving `backtest.engine`'s `ReplayCursor`/
`BacktestOrderExecutor`/`BacktestLedger` classes directly (bypassing `run_backtest()`'s own
pipeline-cycle orchestration, which cannot express a forced 1-bar exit), reusing
`strategy.runner.compute_stop_distances()` unmodified for a nominal SL/TP
(`sl_atr_mult=tp_atr_mult=50`, `min_stop_distance_fraction_of_price=0.05`) sized wide enough to
avoid triggering inside one bar. Confirmed clean: 0 SL/TP leaks in all 12
window x cost-setting x timeframe cells (every trade closed "MANUAL", i.e. the intended 1-bar
exit, not the nominal stop) — full 19,984-trade samples throughout, no min-sample concern
anywhere. Offline only, per this session's instruction: no MCP call of any kind (reused the
already-on-record static `SymbolInfo`, same one
`run_demo_execution_backtest_regime_filter_test_window_validation_0013.py` uses).

**Results** (both timeframes, EARLY/MIDDLE/RECENT windows, zero-cost vs. real-cost
`spread_multiplier=1`):

| Timeframe | Window | Zero-cost expectancy | Real-cost expectancy | Beats all 3 baselines, zero-cost | Beats all 3 baselines, real-cost |
|---|---|---|---|---|---|
| M15 | EARLY | +0.0000 R | -0.0001 R | No | No |
| M15 | MIDDLE | +0.0000 R (z=3.12, outside random range) | -0.0000 R | Yes | Yes |
| M15 | RECENT | +0.0000 R (z=2.14) | -0.0001 R | Yes | Yes |
| H1 | EARLY | +0.0000 R | -0.0004 R | No | No |
| H1 | MIDDLE | +0.0000 R (z=-0.00) | -0.0004 R | No | No |
| H1 | RECENT | +0.0000 R (z=1.24) | -0.0001 R | Yes | Yes |

The decisive fact, not captured by the "beats baselines" column alone: **real expectancy is
negative in all 6 real-cost cells, every window, both timeframes, no exception.** Where "beats
all 3 baselines" reads Yes, it means the fade rule loses *less* than always-long/always-short/
random — none of the four (real signal or any baseline) is ever profitable after cost in any
window tested. "Beats baselines" never once coincides with a positive real-cost expectancy.
Zero-cost expectancy is also economically negligible everywhere (rounds to +0.0000 R in every
cell) even where statistically distinguishable from the random baseline (M15 MIDDLE/RECENT, H1
RECENT) — consistent with Experiment 4's own flag that the underlying 1-bar runs-test effect is
"economically negligible" even where statistically real.

**Classification, applying the pre-registered A/B/C/D rule exactly as specified:**
- **M15 — B.** EARLY window fails to beat baselines at either cost setting; MIDDLE/RECENT beat
  all three baselines at both zero- and real-cost. Not full 3-window consistency (misses the A
  bar), not a clean zero-cost-only pattern either (misses the C bar, since it's not fully erased
  by cost — MIDDLE/RECENT still nominally "beat" at real cost too, just while both are
  themselves negative).
- **H1 — B.** Only RECENT beats all three baselines at either cost setting; EARLY and MIDDLE
  fail both zero-cost and real-cost. Weaker support than M15.

**Recommendation: ABANDON the naive "fade last bar, fixed 1-bar hold" rule for both M15 and H1**,
despite the literal B classification. The pre-registered A/B/C/D rubric was built around a
binary "beats baselines" test and doesn't by itself surface the more important number: real-cost
expectancy for the actual signal is negative in every single window tested, for both candidates,
with no exception. A rule that never produces a positive real-cost expectancy in 6/6 tested
windows is not "weak but promising" in any substantive sense — the confirmed runs-test structure
from Experiment 4 does not translate into a tradeable economic edge at this mechanical
translation (fade-last-bar, 1-bar hold). This closes out the M15/H1 mean-reversion research
thread opened by Experiment 4: statistically real (Exp3/4), but not exploitable as tested (Exp5).
Does not rule out a differently-shaped strategy around the same statistical structure (different
entry filter, position sizing, or holding period) — that would be a new, separately-scoped
research thread, not a continuation of this one.

```
Full run: var/experiment5_output.log (this session, 2026-08-14). No pytest changes — no src/
file touched, one new script only.
```

## What this investigation deliberately did not do

No Step 7 run (run #11 or any other). No Live Pilot work (XAUUSD research, sizing, margin
guards). No live/demo order of any kind. No production `RunnerStrategyConfig`/`GridStrategyConfig`
default changed — every config instance built across all eight new scripts is local to that
script. No parameter search/sweep beyond the pre-specified, fixed concurrency set in Experiment 1
and the pre-specified, fixed horizon set in Experiments 3–4 — none expanded after seeing results.
Experiment 5's harness/costs/baselines/windows were fixed at design time (2026-08-13) and run
unmodified (2026-08-14) — no tuning after seeing intermediate results.

## Overall status (updated 2026-08-14): M15/H1 mean-reversion thread also closed out

Experiment 5 completes the escalation this checkpoint has followed since Experiment 2: MACD-sign
directional (Exp2 — C, no edge) → M1 price action itself (Exp3 — C, mixed/unstable) → M15/H1
price action (Exp4 — B, confirmed statistical structure) → M15/H1 traded as a mechanical rule
(Exp5 — B by the letter of the pre-registered rubric, but ABANDON in substance: real-cost
expectancy negative in 6/6 tested window×timeframe cells). No candidate examined across this
whole investigation (grid or runner, M1/M15/H1, real signal or best-supported alternative) has
produced a real-cost-positive expectancy in any independently-tested window. The
execution/risk-management shape (Phase 9 Stage A/B/C) remains sound and reusable; the
signal-search premise specifically — BTCUSD, MACD-sign or 1-bar-fade, at any of the three
timeframes tried — is what's exhausted, not directional trading in general. Next step, if
pursued, is a new, separately-scoped research thread (different instrument, different signal
shape, or a non-mechanical variant of the fade idea), not a continuation of this one.

## Continuation plan for the next session

Experiment 5 is done; this checkpoint's own investigation has no further pre-scoped open item.
Before starting a new signal-search thread, revisit whether BTCUSD is the right instrument at
all (`docs/LIVE_PILOT_PREPARATION_CHECKPOINT.md` already gathered some comparative XAUUSD data
for an unrelated reason — worth a look before assuming BTCUSD-only continues) and whether a non-
mechanical variant of the fade signal (position sizing by structure strength, a confirmation
filter, asymmetric SL/TP) is worth its own pre-registered design before building anything. Do not
adopt any production parameter from this investigation without separate, explicit approval and
out-of-sample confirmation, per Phase 8's own standing discipline — none of Experiments 1–5 found
grounds to recommend one anyway.

```
pytest -q -> 556 passed, unaffected (no src/ file changed this whole investigation; eight new
one-off scripts + doc updates only)
```

**Files changed across this whole investigation (2026-08-13 afternoon session, Experiment 5
added 2026-08-14)**:
`scripts/run_demo_execution_check_20260813_runner_param_split.py` (new),
`scripts/run_demo_execution_check_20260813_live_window_backtest.py` (new),
`scripts/run_demo_execution_check_20260813_experiment1_concurrency.py` (new),
`scripts/run_demo_execution_check_20260813_experiment2_edge_test.py` (new),
`scripts/run_demo_execution_check_20260813_experiment3_randomwalk.py` (new),
`scripts/run_demo_execution_check_20260813_experiment4_cache_seed.py` (new),
`scripts/run_demo_execution_check_20260813_experiment4_randomwalk_m15_h1.py` (new),
`scripts/run_demo_execution_check_20260813_experiment5_cost_edge_test.py` (new, 2026-08-14),
`docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md` (rows 3–4 and the "How to use this" note updated),
this checkpoint doc, `AGENTS.md`. `var/market_data/BTCUSD_M1.csv` (re-seeded),
`var/market_data/BTCUSD_M15.csv` (new), `var/market_data/BTCUSD_H1.csv` (new),
`var/experiment5_output.log` (new, 2026-08-14) — all machine-local, gitignored, not tracked.
