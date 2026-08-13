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

## Overall recommendation: REDESIGN (not KEEP, not full ABANDON)

KEEP is ruled out — no edge is not a basis for production use. Full ABANDON is broader than what
was actually tested — this investigation rules out one specific naive signal (MACD-sign, no
filter, no regime-awareness), not the runner's execution/risk-management shape (ATR-based SL/TP
sizing, position-limit guard, exposure caps, cost model, backtest harness), which is sound and
reusable regardless of what drives entries.

**Next research question (not parameter values)**: is there *any* directional/predictive signal
capable of pushing win rate meaningfully above the ~33–34% fair-game-plus-costs threshold this
bracket shape requires on BTCUSD at this timeframe — or is BTCUSD M1 price action close enough to
a driftless random walk at this horizon that *any* momentum/trend-following directional signal is
structurally unable to clear that bar, regardless of design? If the latter, the useful next
question becomes whether a fundamentally different strategy shape is needed, not a better signal
within the current one. Not yet scoped as a formal next experiment — awaiting direction.

## What this investigation deliberately did not do

No Step 7 run (run #11 or any other). No Live Pilot work (XAUUSD research, sizing, margin
guards). No live/demo order of any kind. No production `RunnerStrategyConfig`/`GridStrategyConfig`
default changed — every config instance built across all four new scripts is local to that
script. No parameter search/sweep beyond the pre-specified, fixed concurrency set in Experiment 1.

```
pytest -q -> unaffected (no src/ file changed; four new one-off scripts + one doc update only)
```

**Files changed this entry**: `scripts/run_demo_execution_check_20260813_runner_param_split.py`
(new), `scripts/run_demo_execution_check_20260813_live_window_backtest.py` (new),
`scripts/run_demo_execution_check_20260813_experiment1_concurrency.py` (new),
`scripts/run_demo_execution_check_20260813_experiment2_edge_test.py` (new),
`docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md` (rows 3–4 and the "How to use this" note updated),
this checkpoint doc, `AGENTS.md`. `var/market_data/BTCUSD_M1.csv` (re-seeded, machine-local,
gitignored, not tracked).
