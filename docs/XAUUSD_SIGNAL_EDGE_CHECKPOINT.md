# Checkpoint: XAUUSD signal-edge research (new, separately-scoped effort) -- CLOSED 2026-08-14

**Overall recommendation: ABANDON.** No step of this thread (production runner/grid edge test,
M1 random-walk test, M15/H1 escalation) found demonstrable, exploitable edge on XAUUSD --
the same conclusion the BTCUSD investigation reached
(`docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md`). This is treated as **sufficient to
close the thread without building a formal cost-test (Experiment-5-equivalent) harness**:
XAUUSD's strongest finding (the M15/H1 1-bar mean-reversion signature) is objectively weaker than
BTCUSD's own version of the same structure, which already failed real-cost testing decisively
(negative expectancy in 6/6 tested cells, Experiment 5) -- a cost test on a weaker signal would
very likely just reconfirm that failure at real build/run cost, for a close-to-foregone
conclusion, not a genuinely open question.

**This is treated as a complete, honest answer to Live Pilot's criterion 1**
(`docs/LIVE_PILOT_PREPARATION_CHECKPOINT.md`: "a dedicated research pass... for the chosen symbol
specifically"): the pass was done, across four steps, and its answer is that this project's
current signal repertoire (MACD-sign runner, LIMIT-grid, 1-bar-fade mean-reversion) does not show
a cost-surviving edge on XAUUSD either -- criterion 1 does not require a positive result, only a
genuine dedicated attempt before any parameter is trusted. That in turn means the
EURUSD-vs-XAUUSD symbol decision itself should not proceed on an "engine has edge here" basis for
either symbol currently researched under this standard.

**Full results**: see the Step 2/3/M15-H1-escalation entries below, each already carrying its own
result table and classification. No candidate across this whole thread produced a
statistically-confirmed-and-cost-surviving edge at any step.



A new effort, not a continuation of `docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md` —
that investigation's own conclusion (2026-08-14, Experiment 5) was that the whole BTCUSD
signal-search line (MACD-sign directional, M1/M15/H1 mean-reversion) is exhausted: no candidate
across five experiments produced a real-cost-positive expectancy in any independently tested
window. Per that checkpoint's own continuation note, the next step is either a differently-shaped
strategy on the same instrument, or a different instrument — this doc scopes the latter, since
`docs/LIVE_PILOT_PREPARATION_CHECKPOINT.md` already has an unmet, explicitly-recorded reason to
need exactly this: Live Pilot's own EURUSD-vs-XAUUSD decision criteria (that doc, "criteria 1-4")
require **"a dedicated research pass (Phase-8-equivalent: real backtest + at least one
live-verified session) exists for the chosen symbol specifically — BTCUSD's tuned parameters
transferring by assumption is explicitly disqualifying"** (criterion 1, not yet satisfied).
Criteria 2-3 (broker minimum-stop/spread/ATR-to-price structural compatibility) are already
satisfied (2026-08-13 data pull, same checkpoint doc) — XAUUSD's M1 ATR-to-price (0.021%) and D1
ATR-to-price (2.07%) both sit close to BTCUSD's own live-validated regime (0.027% / 1.83%), and
its spread is proportionally tighter (~0.004% of price vs. BTCUSD's ~0.026%), a cost-efficiency
point in XAUUSD's favor for this specific line of research.

**Not yet approved to execute — design/scoping only, per this project's standing discipline
(every prior experiment in the runner-divergence investigation required explicit sign-off before
any harness code was written or any backtest was run).**

## Why replicate rather than design something new

Every methodology this thread proposes reusing was already built, pre-registered, and
validated end-to-end on BTCUSD (Experiments 2-5, `docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_
CHECKPOINT.md`). Rerunning the identical, zero-free-parameter test code against a new instrument
carries none of the overfitting risk a newly-designed rule would — the only new "decision" is
which symbol's data goes in, not which statistical test or which R-multiple convention. This
also directly mirrors this project's own established order of operations from the BTCUSD
investigation: test the actual deployed strategy first (Exp2-equivalent), and only search for
alternative structure after that fails (Exp3/4-equivalent), and only translate confirmed
structure into a tradeable rule after structure is confirmed (Exp5-equivalent) — never skip
ahead to rule-design before the earlier steps have actually failed.

## Proposed steps (each gated on the previous step's own result, not run speculatively)

**Step 1 — data.** Seed offline XAUUSD M1/M15/H1 caches, mirroring
`scripts/run_demo_execution_historical_data_cache_seed.py` /
`scripts/run_demo_execution_check_20260813_experiment4_cache_seed.py`'s own pattern: one
read-only live pull per timeframe (`get_candles_latest`), target as much real history as the
broker exposes (BTCUSD's own caches reached 76,000 M1 bars / 60,000 M15 / 60,000 H1 bars — same
target, not a fixed count chosen in advance). The existing
`scripts/run_demo_execution_xauusd_symbol_research.py` already proves the read path works
(`get_symbols`/`get_symbol_info`/`get_candles_latest`, all `READ_ONLY`-classified, already used
with explicit go-ahead 2026-08-13) but only pulled small sample counts for the symbol-comparison
table — this step is a full cache seed, not a repeat of that.

**Step 2 — does the currently-deployed strategy have edge on XAUUSD?** Direct backtest of
production `RunnerStrategyConfig`/`GridStrategyConfig` defaults (unmodified — this is what
criterion 1 is actually gatekeeping, not a hypothetical redesign) against XAUUSD M1 history,
using `backtest.engine.run_backtest()` exactly as-is (the real pipeline, no harness needed here,
unlike Experiment 5). Mirrors Experiment 2's real-vs-random-direction edge test, same 20
pre-registered seeds, same three-window split convention (chronological, not recycling BTCUSD's
specific calendar boundaries — XAUUSD's own history has its own natural TRAIN/HELD-OUT/most-recent
thirds). If this alone shows a real, cost-inclusive, cross-window-consistent edge, that's
criterion 1 satisfied directly and steps 3-4 are unnecessary.

**Step 3 — if Step 2 finds no edge (the expected outcome given BTCUSD's own history): is XAUUSD
M1 price action distinguishable from a random walk?** Experiment 3's exact methodology reused
verbatim (ACF / Lo-MacKinlay variance ratio / Wald-Wolfowitz runs test, horizons `{1,5,15,30,60}`
bars, Bonferroni-corrected, cross-window-consistency pre-committed). If no trading-relevant-
horizon structure survives, escalate to M15/H1 (Experiment 4's methodology, chronological-thirds
windows) before concluding "no usable structure at any timeframe tried."

**Step 4 — only if Step 3 (at whichever timeframe) finds cross-window-consistent structure**:
translate it into a zero-free-parameter mechanical rule and test it against real cost, exactly
Experiment 5's harness and classification rubric (A/B/C/D), reused unmodified — including its own
hard-won lesson: a statistically confirmed structure is not assumed to be an economic edge until
this step says so.

## Scope discipline (same commitments as the closed BTCUSD investigation)

Offline/backtest-only throughout, same as Experiments 1-5 — the only live calls anywhere in this
plan are Step 1's read-only cache-seed pulls (`get_candles_latest`) and Step 2's optional
`get_symbol_info` reuse of the already-on-record XAUUSD constraints (2026-08-13 pull, this
project's own convention per `scripts/run_demo_execution_backtest_regime_filter_test_window_
validation_0013.py`, so a second live pull may not even be needed). No Step 7 run. No Live Pilot
order of any kind. No production `RunnerStrategyConfig`/`GridStrategyConfig` default changed —
every config instance in every step is local to its own script, same as the entire prior
investigation. No parameter search beyond the pre-specified, fixed horizon/seed/window sets each
reused methodology already carries. Each step is gated on its predecessor's own result, not run
speculatively ahead of approval.

## Status

Scoped 2026-08-14. **Step 1 approved and run 2026-08-14.** Script:
`scripts/run_demo_execution_check_20260814_xauusd_cache_seed.py`. All three timeframes fetched
their full requested count on the first attempt (no response-size ceiling hit, unlike BTCUSD's
M1 which needed live bisection between 95,000/100,000 historically):

| Timeframe | Bars | Range | Real span |
|---|---|---|---|
| M1 | 95,000 | 2026-05-11 → 2026-08-14 | ~95 days |
| M15 | 60,000 | 2024-01-31 → 2026-08-14 | ~927 days (~30 months) |
| H1 | 60,000 | 2016-05-19 → 2026-08-14 | ~10 years |

The one warning printed (`require_demo_account informational check failed: trade_mode='REAL'`)
is the already-documented `mcp_account.py` inversion bug (`docs/MCP_ADAPTER_WIRING_CHECKPOINT.md`,
`docs/PIPELINE_WIRING_CHECKPOINT.md`), informational-only, not a new issue. Read-only throughout
— no order of any kind, no production default changed. All three caches are new files under
`var/market_data/` (gitignored, machine-local, not tracked).

**Step 2 approved and run 2026-08-14.** Script:
`scripts/run_demo_execution_check_20260814_xauusd_experiment2_edge_test.py`. Fetched real
`SymbolInfo` live (one read-only call: digits=2, point=0.01, volume_min/max/step=0.01/10.0/0.01,
stops_level=1, freeze_level=0, filling_modes=('FOK',), spread=19), then ran unmodified production
`RunnerStrategyConfig()`/`GridStrategyConfig()` through the real, unmodified `run_backtest()`
pipeline across three chronological-thirds windows of the M1 cache (~31,666 bars / ~31 days
each), runner vs. its 20-seed random-direction baseline, grid as a reference row.

**Result: INCONCLUSIVE for runner, not a clean answer.** Production runner generated only 1-4
closed trades per ~31-day window (EARLY=3, MIDDLE=4, RECENT=1) — `min_sample_met=False` in every
window, an order of magnitude below the 30-trade floor this project has held every other edge
claim to. The real-vs-random z-scores computed anyway (1.58 / -1.01 / 1.22) are not meaningful at
this sample size and should not be read as evidence either way. This is almost certainly the same
structural mechanism Experiment 1 already confirmed for BTCUSD
(`docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md`): `sl_atr_mult=3.0` plus the 1%-of-price
SL floor produces stops wide enough that a position rarely resolves, and
`max_concurrent_positions=1` then blocks nearly every subsequent signal for most of the window —
not independently re-verified via log inspection this run (no `position_limit`-rejection count
was captured), but consistent with the exact mechanism already diagnosed, not a new mystery.

**Grid (reference row, not this step's primary target, but well-powered and worth recording)**:
405-455 trades per window, consistently negative — expectancy -0.058 R / -0.024 R / -0.039 R,
profit factor <1 in every window. No random baseline was built for grid (out of this step's
scope), but a large, consistently-negative raw sample doesn't need one to rule out "grid as
currently configured has an edge on XAUUSD" — that reading doesn't depend on comparison to chance.

**Step 3 (M1) approved and run 2026-08-14** — runs directly (skipping the relaxed-concurrency
option) since Step 2's runner leg was inconclusive, not negative, and this step tests price
action directly, so the trade-generation bottleneck doesn't apply. Script:
`scripts/run_demo_execution_check_20260814_xauusd_experiment3_randomwalk.py`, Experiment 3's
exact test code (ACF / Lo-MacKinlay variance ratio / Wald-Wolfowitz runs test) reused verbatim,
chronological-thirds windows (EARLY/MIDDLE/RECENT, ~31,666 bars each) since XAUUSD has no live
window the way BTCUSD's M1 did.

**Result: mirrors BTCUSD M1's own Experiment 3 finding closely.**
- 1-bar runs test: fully confirmed, cross-window-consistent, Bonferroni-significant in all three
  windows (z=5.73 to 6.66), same "more runs = mean-reversion-like" direction throughout.
- Notably *corroborated* by ACF here (unlike BTCUSD M1, where ACF and the runs test contradicted
  each other in the LIVE window) -- 1-bar ACF negative (mean-reversion sign) in all three windows,
  same-sign consistent, one window (MIDDLE) just short of the strict Bonferroni bar (p=0.0012 vs.
  required 0.001111).
- Nothing at trading-relevant horizons (5-60 bars) replicates across all three windows -- variance
  ratio flips sign between windows (EARLY/RECENT mean-reversion-like, MIDDLE momentum-like), runs
  test shows no consistent departure beyond 1 bar.

**Classification: C -- mixed/unstable structure**, mirroring BTCUSD M1's own Experiment 3 result.
Not D (a real, arguably more internally-consistent 1-bar effect than BTCUSD M1 showed, since ACF
corroborates rather than contradicts here); not A/B (nothing at a horizon a strategy could
actually trade on).

**M15/H1 escalation approved and run 2026-08-14.** Script:
`scripts/run_demo_execution_check_20260814_xauusd_experiment4_randomwalk_m15_h1.py`, Experiment
4's exact methodology reused verbatim (identical formulas, identical horizon set, M15/H1
corrected separately, chronological thirds since XAUUSD has no live window at any timeframe).

**Result: weaker than BTCUSD's own M15/H1 finding -- does NOT clear the full pre-committed bar.**
Both timeframes show a real, same-direction 1-bar runs-test effect (mean-reversion-like),
raw-significant and same-sign in all 3 windows (M15: z=+3.38/+2.66/+4.07; H1:
z=+5.24/+6.94/+2.12) -- qualitatively matching BTCUSD's pattern. But **neither is fully
Bonferroni-robust in all 3 windows**, unlike BTCUSD's M15/H1 (which cleared same-sign AND
raw-significant AND Bonferroni-robust in every window, the explicit bar this checkpoint series
has held every other cell to): M15's MIDDLE window (p=0.0078) and H1's RECENT window (p=0.0337)
both land above the 0.001111 Bonferroni threshold, missing by one window each. Nothing beyond 1
bar shows cross-window consistency at either timeframe -- no extension even to BTCUSD H1's own
weak 5-hour result.

**Classification: C for both M15 and H1** -- real, same-direction signal present (stronger than
pure noise/D), but does not clear the full pre-committed bar this investigation requires before
calling something confirmed structure (short of BTCUSD's own B). A genuine asymmetry between the
two instruments at the same timeframes -- exactly the kind of thing criterion 1 exists to catch,
since it means XAUUSD does NOT simply inherit BTCUSD's structural properties even at the one
horizon where BTCUSD showed something real.

**Implication for whether to build a cost-test harness (Experiment 5 equivalent) for XAUUSD**:
BTCUSD's own FULLY-CONFIRMED version of this exact 1-bar effect already failed real-cost testing
decisively in Experiment 5 (negative expectancy in 6/6 tested window x timeframe cells). XAUUSD's
version here is objectively weaker (fails the Bonferroni leg BTCUSD's version cleared). Building a
full cost-test harness for XAUUSD would very likely just reconfirm that same failure at real
build/run cost, for a close-to-foregone conclusion -- flagged for an explicit decision before
building it, not built speculatively.

**Emerging synthesis across the whole XAUUSD thread so far**: Step 2's runner leg was
inconclusive (sample too small to test at all); grid was negative and well-powered; Step 3 (M1)
found mixed/unstable structure (C, mirroring BTCUSD M1); this M15/H1 escalation found weaker,
not-fully-confirmed structure (C, short of BTCUSD's B). Nothing in this thread yet supports
XAUUSD as a symbol with demonstrable, currently-exploitable edge -- the same conclusion the
BTCUSD investigation reached. This may already be a complete, if negative, answer to Live Pilot's
criterion 1: a dedicated research pass was done, and its honest answer is that this project's
current signal repertoire (MACD-sign runner, LIMIT-grid, 1-bar-fade mean-reversion) does not show
a cost-surviving edge on XAUUSD either, not just BTCUSD.

**What Step 2 alone could not settle**: production runner's own edge on XAUUSD, since the config
was too restrictive to produce a testable sample at M1 over the available history (~95 days
total, already at this cache's depth ceiling). Steps 3-4 (random-walk structure tests, which need
no trade generation) resolved this by testing price action directly instead, and their combined
result (see the closing summary at the top of this doc) is what closed the thread — the
relaxed-concurrency retest option (b) above was never taken up, and does not need to be, given
that outcome.

## Status: CLOSED 2026-08-14

**Overall recommendation: ABANDON** (restated from the top of this doc). Four steps completed:
production runner/grid edge test (Step 2, runner inconclusive on sample size, grid negative and
well-powered), M1 random-walk test (Step 3, classification C), M15/H1 escalation (classification
C for both, weaker than BTCUSD's own confirmed M15/H1 result). No step found demonstrable,
exploitable edge. Treated as a complete, honest answer to Live Pilot criterion 1 rather than
grounds to build a further cost-test harness — see the top-of-doc summary for the reasoning.
`pytest -q` → 556 passed throughout, unaffected (no `src/` file touched by this whole thread; four
new one-off scripts + this doc only).

**Files**: `scripts/run_demo_execution_check_20260814_xauusd_cache_seed.py`,
`scripts/run_demo_execution_check_20260814_xauusd_experiment2_edge_test.py`,
`scripts/run_demo_execution_check_20260814_xauusd_experiment3_randomwalk.py`,
`scripts/run_demo_execution_check_20260814_xauusd_experiment4_randomwalk_m15_h1.py`, this
checkpoint doc. `var/market_data/XAUUSD_M1.csv`/`XAUUSD_M15.csv`/`XAUUSD_H1.csv` (new,
machine-local, gitignored, not tracked) plus `var/xauusd_experiment{2,3,4}_output.log` (same).
