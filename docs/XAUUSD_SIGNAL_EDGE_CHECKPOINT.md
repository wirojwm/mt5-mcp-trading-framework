# Checkpoint: XAUUSD signal-edge research (new, separately-scoped effort)

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

**Next step (Step 2, not yet approved): backtest the current production runner/grid config,
unmodified, against this XAUUSD M1 cache** — Experiment 2's edge-test methodology reused
verbatim, three chronological-thirds windows of XAUUSD's own ~95-day M1 history (not recycling
BTCUSD's calendar boundaries). Awaiting explicit go-ahead before building/running.
