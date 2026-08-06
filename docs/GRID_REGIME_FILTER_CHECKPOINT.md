# Checkpoint: Grid regime filter — CLOSED, negative result (2026-08-06)

**This effort is closed.** Both threshold candidates it produced
(`max_entry_efficiency_ratio=0.2` and `0.013`) were rejected by out-of-sample validation. No
production default changed. See "Effort closed — negative result, user-approved" near the end of
this doc for the full outcome and reasoning before proposing any follow-on work.

A new, separately-scoped effort, not one of this project's numbered phases (0–7) and not part of
Phase 8 itself — same relationship "wire real adapters" had to Phase 6, or "pipeline wiring" had
to Phases 6/7: motivated by a Phase 8 finding, but its own explicitly-approved unit of work, per
`AGENTS.md`'s required workflow ("explain the goal, list files to create/change, identify risks
and assumptions — before editing... stop and wait for explicit approval before starting the next
phase"). The sections below are kept as originally written (the proposal, then each step's
real results) for full history — read "Effort closed" for the final status.

## Motivation

`docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md`, Step 7: grid's negative expectancy is not uniform
across market conditions. Classified grid's 119 real training-window trades by Kaufman's
Efficiency Ratio (`features/regime.py`, already built and tested) at entry, split at the median:

| | trades | win rate | expectancy | max drawdown |
|---|---|---|---|---|
| ranging (ER < median) | 58 | 72.4% | −0.102 R | 7.920 R |
| trending (ER ≥ median) | 61 | 41.0% | −0.492 R | 30.720 R |

Grid does not have a positive edge in either regime — this is a damage-limitation idea, not a
"make grid profitable" claim. The proposal: skip new grid entries when the market is trending
(ER above some threshold), on the theory that avoiding the worse-performing regime's trades
improves grid's blended expectancy, the same honest framing as runner's SL/TP widening (Phase 8:
real, evidence-backed risk reduction, not a profitability claim).

## Design

**Where it lives**: `pipeline/grid_cycle.py`, not `strategy/grid.py`. `compute_grid_levels()`'s
own docstring is explicit that grid "seeds both sides regardless of directional belief" — it has
no "skip this cycle" concept today, and changing that contract would ripple into every existing
caller/test. The established precedent for a bars-derived skip decision already exists one layer
up: `runner_cycle.py`'s FLAT-signal check (`if intent is None: ... return None`), which runs
immediately after `bars = await market_data.get_bars(...)`, before any further reads. A grid
regime filter would follow the identical shape:

```python
bars = await market_data.get_bars(symbol, timeframe, bars_count)

if grid_config.max_entry_efficiency_ratio is not None:
    er = efficiency_ratio(bars, grid_config.efficiency_ratio_period)
    if er >= grid_config.max_entry_efficiency_ratio:
        _logger.info("[GRID] %s regime filter: efficiency_ratio=%.4f >= max=%.4f, "
                      "trending, skipping cycle", symbol, er, grid_config.max_entry_efficiency_ratio)
        return []
```

placed right after the existing `bars = await market_data.get_bars(...)` line in
`run_grid_cycle()`, before `symbol_info`/`tick` are fetched — same ordering benefit
`runner_cycle.py` already gets (skips the rest of the cycle's reads too, not just the strategy
call).

**Config**: two new fields on `GridStrategyConfig`, both additive and backward-compatible:
- `max_entry_efficiency_ratio: Optional[float] = None` — `None` (default) means the filter is
  **off**; every existing caller (dry-run pipeline, all current tests, every smoke test) is
  unaffected unless it explicitly opts in. Matches this codebase's own established convention for
  additive config (e.g. `state_store` on `run_grid_cycle()`/`run_runner_cycle()`).
- `efficiency_ratio_period: int = 14` — matches `atr_period`'s own default, no new magic number.

**Scope of the gate**: blocks NEW submissions only, for both grid sides together (BUY_LIMIT and
SELL_LIMIT) — Efficiency Ratio characterizes the whole market window, not a side, and grid
already evaluates/submits both sides as one cycle decision. It does **not** touch existing open
positions or pending orders in any way — exactly the same scope every other guard in this
codebase already has (`check_exposure_cap`, `check_duplicate_order`, `check_position_limit` all
only ever block new submissions, never manage existing ones). A position or pending order already
live when the regime turns trending keeps its existing SL/TP and is exit-managed normally.

**No look-ahead risk beyond what's already proven**: the filter reads the exact same `bars` the
cycle already fetches via `MarketDataSource.get_bars()` — live, this is real-time data with no
future information possible; in the backtest engine, this is the same
`BacktestMarketDataSource.get_bars()` → `ReplayCursor.visible_bars()` path `grid_cycle.py`
already goes through today, already covered by the engine's own dedicated look-ahead test. No new
adapter/MCP call, no new risk category.

## Open design points, decided provisionally here — flag if you'd rather choose differently

1. **Threshold value must be tuned and validated, not hardcoded from Step 7's median.** Step 7's
   0.3847 is a single-window, single-split observation — using it directly as a hardcoded
   production threshold would repeat exactly the overfitting risk Step 5/6 exists to catch (see
   grid's own rejected `step_mult=0.25` candidate, which looked good on the training window and
   didn't survive the held-out test). Proposed: sweep a small set of threshold candidates against
   the training window (reusing the existing sweep-script pattern), then validate the winner
   against the held-out test window before ever treating it as more than a candidate.
2. **Gate blocks both sides together**, not per-side — Efficiency Ratio has no directional
   component, so there's no principled way to apply it to only one side.
3. **Filter defaults to OFF** (`max_entry_efficiency_ratio=None`) — zero behavior change for any
   existing test or script unless a caller explicitly opts in, matching this codebase's own
   backward-compatibility convention throughout.
4. **Existing positions/pending orders are never touched** by this filter — skip-new-entries only,
   same scope as every existing guard.

## Proposed steps, smallest/lowest-risk first

| Step | Scope | Entry criteria | Exit criteria | Key risk |
|---|---|---|---|---|
| 1 | Threshold sweep against the TRAINING window only (reuse the existing sweep-script pattern; no production code change) | This doc reviewed/approved | A candidate `max_entry_efficiency_ratio` value, full sweep table archived, not just the "best" number | Overfitting to this one training window — the entire reason Step 3 exists |
| 2 | Build the opt-in filter (`GridStrategyConfig` fields + `grid_cycle.py` gate), unit + integration tested, default OFF | Step 1 has a candidate | Filter code merged; new tests proving both the skip path and the unaffected-pass-through path (`max_entry_efficiency_ratio=None`); every existing test still passes unmodified | Must not change behavior for any existing caller that doesn't opt in |
| 3 | Out-of-sample validation: re-run the backtest with the candidate threshold against the HELD-OUT test window (same discipline as Phase 8 Step 6) | Step 2 built | Honest report — does the candidate threshold's improvement hold on unseen data | Could fail to validate (a legitimate, useful outcome, same as grid's rejected `step_mult` candidate) |
| 4 (only if Step 3 validates) | Adopt as the new `GridStrategyConfig` production default | Step 3 validates | Its own separate, explicitly-approved production-code change | None beyond this project's normal production-change discipline |
| 5 (only if Step 4 happens) | Live verification via a self-cleaning smoke test, same pattern as runner's SL/TP change | Step 4 adopted | A real demo-account cycle proving the filter behaves correctly end-to-end | Standard live-testing pause/go-ahead rules apply — its own explicit go-ahead |

## Explicitly not in this effort

- No change to `strategy/grid.py`'s `compute_grid_levels()` or `GridLevels`' contract.
- No change to runner or any other strategy.
- Steps 4–5 (production adoption, live verification) are not automatic even if Step 3 validates —
  each is its own explicitly-approved step, same discipline as every production change this
  project has made so far.
- Does not revisit or replace Step 7's regime analysis itself — this effort consumes that finding,
  it doesn't redo it.

## Step 1 — threshold sweep: done, a genuine (non-edge) candidate found

Provisional design choices in this doc were approved as written. `backtest/engine.py`'s
`run_backtest()` grew two new, opt-in, default-`None` parameters
(`grid_max_entry_efficiency_ratio`/`grid_efficiency_ratio_period`) — deliberately NOT a change to
`GridStrategyConfig`/`pipeline/grid_cycle.py` (see the module docstring for the full reasoning:
this lets the sweep dynamically, correctly simulate the filter's real effect — a skipped cycle
genuinely frees up exposure-cap slots for later cycles — without building the production change
before a threshold is chosen and validated). 3 new unit tests in `tests/unit/test_backtest_engine.py`:
`grid_max_entry_efficiency_ratio=None` matches omitting the parameter entirely; `0.0` blocks
essentially all grid submissions while leaving runner untouched; an impossibly high threshold
(`999.0`) is a true no-op, matching unfiltered behavior exactly.

`scripts/run_demo_execution_backtest_regime_filter_sweep.py` (new): one real read-only
`get_symbol_info` call, then fully offline against the training window, candidates spanning Step
7's own observed ER range plus a "no filter" baseline.

**First pass (0.2–0.7)** found `0.2` — the low EDGE of that range — as the best-expectancy point,
monotonically improving all the way down to it (every threshold from 0.2–0.5 beat the unfiltered
baseline; 0.6/0.7 barely filtered anything and were actually slightly worse than no filter at
all). Same caution already applied to grid's `step_mult` and runner's `sl_atr_mult` sweeps
earlier in Phase 8: an edge-of-range best point needs widening before it's trusted.

**Widened to 0.05–0.7, full results, training window**:

| threshold | trades | win rate | expectancy | max drawdown |
|---|---|---|---|---|
| unfiltered (baseline) | 119 | 56.3% | −0.302 R | 37.120 R |
| 0.05 | 139 | 55.4% | −0.313 R | 43.761 R |
| 0.1 | 115 | 60.9% | −0.245 R | 28.680 R |
| 0.15 | 139 | 61.9% | −0.233 R | 32.600 R |
| **0.2** | 141 | 63.8% | **−0.209 R** | 29.920 R |
| 0.3 | 143 | 60.8% | −0.246 R | 35.760 R |
| 0.4 | 141 | 59.6% | −0.261 R | 37.560 R |
| 0.5 | 129 | 58.9% | −0.269 R | 35.241 R |
| 0.6 | 127 | 55.1% | −0.317 R | 40.680 R |
| 0.7 | 121 | 53.7% | −0.334 R | 40.880 R |

**This resolves the edge concern**: `0.2` is a genuine interior peak, flanked by tested-worse
values on both sides (`0.15` and `0.3` both worse; `0.1`/`0.05` reverse further, `0.05` actually
worse than the unfiltered baseline; `0.4`–`0.7` degrade steadily the other direction) — the same
bounded shape that made runner's `sl_atr_mult=3.0` trustworthy rather than an untested-edge guess.
141 trades comfortably clears both the 30-trade minimum and the 50+ preferred sample size.

**Decided candidate for Step 3: `max_entry_efficiency_ratio=0.2`** (`efficiency_ratio_period=14`,
unchanged) — training-window evidence only. Improvement over the unfiltered baseline: expectancy
−0.302 R → −0.209 R, drawdown 37.120 R → 29.920 R, trade count 119 → 141 (more trades, not fewer
— the exposure-cap-freeing dynamic in effect). **No production default changed** —
`GridStrategyConfig` has no such field yet; this is purely a backtest-engine-level finding.

```
pytest -q                        -> 424 passed (421 previously + 3 new, engine grid_max_entry_efficiency_ratio)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/trading call beyond each sweep's one read-only `get_symbol_info` pull. Process
cleanup confirmed clean.

**Files changed this entry**: `src/mt5_mcp_trading/backtest/engine.py` (modified — new opt-in
`grid_max_entry_efficiency_ratio`/`grid_efficiency_ratio_period` params on `run_backtest()`),
`tests/unit/test_backtest_engine.py` (modified, +3),
`scripts/run_demo_execution_backtest_regime_filter_sweep.py` (new), this checkpoint doc.

## Step 2 — opt-in production filter: built, unit + integration tested

Built exactly as designed above, no deviations. `GridStrategyConfig` (`strategy/grid.py`) gained
`max_entry_efficiency_ratio: Optional[float] = None` and `efficiency_ratio_period: int = 14`.
`compute_grid_levels()`/`GridLevels` were **not** touched — both fields are read only by
`pipeline/grid_cycle.py`'s `run_grid_cycle()`, right after `bars = await market_data.get_bars(...)`
and before `symbol_info`/`tick`/account reads, mirroring `runner_cycle.py`'s own FLAT-signal skip
ordering exactly: when the threshold is set and `features/regime.py`'s `efficiency_ratio(bars,
efficiency_ratio_period)` is `>=` it, the cycle logs and returns `[]` immediately — same
"reject/skip, never raise" convention as every other rejection in this function, and the same
scope as every other guard here (blocks new submissions only, never touches existing open
positions or pending orders).

**Tests** (`tests/integration/test_grid_dry_run_pipeline.py`, +4): filter off by default matches
existing behavior exactly (`GridStrategyConfig()` with no override); a strictly monotonic
("trending", ER≈1.0) fixture with `max_entry_efficiency_ratio=0.5` blocks both sides, nothing
submitted; the existing choppy/ranging `_bars()` fixture (ER≈0.008, used by every other test in
this file) with the same threshold configured but not triggered behaves identically to the
unfiltered baseline — proving a configured-but-inactive filter is a true no-op, not just that
`None` is; a dedicated ordering test (`_RaisingIfCalledMarketDataSource`, raises if
`get_symbol_info`/`get_tick` are ever called) proves the early return actually happens before
those reads, not just before submission. All existing tests in the file pass completely
unmodified (only the shared `_run()` helper gained an optional `grid_config` override parameter,
defaulting to the exact same config every existing call already used).

```
pytest -q                        -> 428 passed (424 previously + 4 new, grid regime filter integration tests)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/trading call — pure code + tests, no script run this entry.

**Files changed this entry**: `src/mt5_mcp_trading/strategy/grid.py` (modified — new
`max_entry_efficiency_ratio`/`efficiency_ratio_period` fields),
`src/mt5_mcp_trading/pipeline/grid_cycle.py` (modified — the gate, right after the bars fetch),
`tests/integration/test_grid_dry_run_pipeline.py` (modified, +4), this checkpoint doc.

## Exact next smallest task

**Step 2 is done.** Next per the step table: **Step 3** — out-of-sample validation. Re-run the
`0.2` candidate against the HELD-OUT test window, now against the REAL `pipeline/grid_cycle.py`
implementation (via `GridStrategyConfig(max_entry_efficiency_ratio=0.2)` passed straight into
`run_backtest()`'s existing `grid_config` parameter — no need for the engine's parallel
simulation hook anymore, though that hook still works and remains available for any future
threshold re-sweeps) rather than the Step 1 sweep's simulation-only path. Report honestly, same
Step 6 discipline: does the training-window improvement hold on unseen data, or does it collapse
the way grid's `step_mult=0.25` candidate did in Phase 8. **No production default changed** —
`GridStrategyConfig`'s own default remains `max_entry_efficiency_ratio=None`; this is still an
opt-in candidate, not adopted anywhere. Needs explicit go-ahead before Step 3's script is written
or run.

## Step 3 — out-of-sample validation: run, and the candidate does NOT validate

`scripts/run_demo_execution_backtest_regime_filter_test_window_validation.py` (new): one real,
read-only `get_symbol_info` call, then fully offline against the held-out 19,000-bar test window
(never read by Step 1's sweep or Step 2's tests). Runs `GridStrategyConfig()` (filter off) and
`GridStrategyConfig(max_entry_efficiency_ratio=0.2)` back-to-back against the identical window —
via the REAL Step-2-built pipeline path this time (the config field, read directly by
`pipeline/grid_cycle.py`), not Step 1's parallel simulation hook.

**Real results, test window, never touched before this run**:

| | grid trades | grid win rate | grid expectancy | grid drawdown |
|---|---|---|---|---|
| filter off (baseline) | 45 | 55.6% | −0.311 R | 14.240 R |
| candidate `max_er=0.2` | 33 | 51.5% | **−0.361 R** | 12.400 R |

**Does not validate.** On the training window this candidate looked clearly better (−0.302 R →
−0.209 R, a 0.093 R gain). Out-of-sample the improvement doesn't just fail to hold — it reverses
into a result worse than doing nothing at all (−0.311 R → −0.361 R, a 0.050 R loss). Trade count
also dropped this time (45 → 33, still above the 30-trade minimum but thin, unlike the training
window where filtering *increased* trade count 119 → 141 — the exposure-cap-freeing dynamic runs
in the opposite direction on this later, structurally different window). Runner's numbers are
byte-for-bit identical in both rows (550 trades, same expectancy/drawdown both times), confirming
the filter stayed correctly isolated to grid, as designed — this isn't a wiring bug, the candidate
genuinely doesn't generalize.

**This is the same overfitting signature grid's `step_mult=0.25` candidate showed in Phase 8 Step
6** — a real, useful negative result, not a failed effort. `max_entry_efficiency_ratio=0.2`
should NOT be adopted as a production default on this evidence.

```
pytest -q                        -> 428 passed (unchanged -- only a new read-only script added)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/trading call beyond the one read-only `get_symbol_info` pull. Process cleanup
confirmed clean.

**Files changed this entry**:
`scripts/run_demo_execution_backtest_regime_filter_test_window_validation.py` (new), this
checkpoint doc.

## Second Step 1 attempt — wider sweep, then a fine-grained probe, found a new tentative candidate

`0.2` was rejected by Step 3. Per the "fresh Step 1, not a continuation" path above, re-swept
`scripts/run_demo_execution_backtest_regime_filter_sweep.py`'s `THRESHOLD_CANDIDATES` against the
training window (still never touching the held-out test window).

**Wide pass (0.01–1.0, 23 candidates)**: `0.9`/`1.0` correctly converged to the exact unfiltered
baseline (confirms filter mechanics at the extreme — no threshold above Step 7's observed max ER
of 0.8209 can ever trigger). `0.01` posted the best expectancy of the entire table by a wide
margin (−0.131 R vs. next-best −0.209 R), but its immediate neighbor `0.02` collapsed straight
back to baseline-like (−0.311 R) — a massive, one-point discontinuity (401 trades at `0.01` vs.
63 at `0.02`) with no smooth transition, unlike `0.2`'s well-behaved earlier neighborhood. Flagged
as a likely noise/instability artifact rather than trusted, pending finer investigation.

**Fine-grained probe (0.005–0.02, 11 candidates)** — this revised that read substantially:

| threshold | trades | win rate | expectancy | max drawdown |
|---|---|---|---|---|
| unfiltered | 119 | 56.3% | −0.302 R | 37.120 R |
| 0.005 | 326 | 68.7% | −0.148 R | 49.200 R |
| 0.007 | 455 | 69.7% | −0.136 R | 62.400 R |
| 0.008 | 489 | 71.0% | −0.120 R | 59.841 R |
| 0.009 | 383 | 70.2% | −0.129 R | 49.679 R |
| 0.01 | 401 | 70.1% | −0.131 R | 52.560 R |
| 0.011 | 433 | 68.8% | −0.147 R | 63.480 R |
| 0.012 | 445 | 67.4% | −0.164 R | 73.001 R |
| **0.013** | 235 | **72.8%** | **−0.098 R** | **22.960 R** |
| 0.015 | 237 | 70.9% | −0.121 R | 28.680 R |
| 0.017 | 53 | 56.6% | −0.298 R | 15.800 R |
| 0.02 | 63 | 55.6% | −0.311 R | 19.600 R |

**Not an isolated spike after all — a real, broad plateau.** Every threshold from `0.005` through
`0.015` beats the unfiltered baseline substantially (expectancy −0.098 to −0.164 R vs. −0.302 R;
win rate 67–73% vs. 56.3%), ten consecutive candidates, not one lucky point. The actual cliff sits
between `0.015` and `0.017`, not between `0.01` and `0.02` as the coarser first pass made it look.
`0.013` is the single best point in the plateau (−0.098 R, 72.8% win rate, 22.960 R drawdown — also
the lowest drawdown in the region) and comfortably clears the 50+ preferred sample size (235
trades).

**Real, honest caveat, not glossed over**: trade count *within* the plateau is noisy and
non-monotonic (235–489, no smooth progression) even though the outcome metrics (expectancy, win
rate) are consistently good throughout — evidence the underlying exposure-cap-freeing dynamic is
genuinely sensitive at this fine a threshold granularity, not that the improved-performance
finding itself is fragile. This is training-window evidence only. **Not validated out-of-sample.
Not adopted. No production default changed** — `GridStrategyConfig.max_entry_efficiency_ratio`
still defaults to `None` everywhere; only the sweep script's candidate list changed.

```
pytest -q -> 428 passed (unchanged -- only sweep-script candidate tuples changed, no production code)
```

No order, no live/trading call beyond each sweep's one read-only `get_symbol_info` pull (same
script, same call already covered by prior approval). Process cleanup confirmed clean.

**Files changed this entry**: `scripts/run_demo_execution_backtest_regime_filter_sweep.py`
(modified — `THRESHOLD_CANDIDATES` widened, then narrowed to a fine-grained probe), this
checkpoint doc.

## Step 3 (second candidate) — out-of-sample validation run for 0.013: does NOT pass acceptance

`scripts/run_demo_execution_backtest_regime_filter_test_window_validation_0013.py` (new): runs
`GridStrategyConfig()` (filter off), `GridStrategyConfig(max_entry_efficiency_ratio=0.2)` (the
already-rejected first candidate, kept in the same run purely as a reproducibility/consistency
check), and `GridStrategyConfig(max_entry_efficiency_ratio=0.013)` back-to-back against the
identical held-out test window, via the real `pipeline/grid_cycle.py` path. **Deliberately fully
offline, zero MCP/MT5 calls** — unlike the first candidate's validation script, this one does not
open a `demo_execution_session` or call `get_symbol_info()`; it reuses the real BTCUSD
`SymbolInfo` already fetched live and recorded in `docs/MCP_ADAPTER_WIRING_CHECKPOINT.md` (static
broker symbol constraints, not live price — unchanged since that fetch and already the same value
every backtest script in this project has used). No order, no live/demo call of any kind this
entry.

**Reproducibility check passed first**: the filter-off and `0.2` rows in this run are
byte-for-bit identical to Step 3's original results for those two configs (45 trades/55.6%/
−0.311 R/14.240 R baseline; 33 trades/51.5%/−0.361 R/12.400 R for `0.2`) — confirms the hardcoded
real `SymbolInfo` substitution changed nothing material, so the new `0.013` row below is trustworthy.

**Real results, held-out test window (never touched by Step 1's sweep for either candidate)**:

| | grid trades | grid win rate | grid expectancy | grid drawdown |
|---|---|---|---|---|
| filter off (baseline) | 45 | 55.6% | −0.311 R | 14.240 R |
| rejected candidate `max_er=0.2` | 33 | 51.5% | −0.361 R | 12.400 R |
| new candidate `max_er=0.013` | 197 | 62.9% | **−0.219 R** | **43.718 R** |

**Training vs. held-out, side by side, for the `0.013` candidate specifically**:

| | trades | win rate | expectancy | max drawdown |
|---|---|---|---|---|
| training, baseline | 119 | 56.3% | −0.302 R | 37.120 R |
| training, `max_er=0.013` | 235 | 72.8% | −0.098 R | 22.960 R |
| **held-out, baseline** | 45 | 55.6% | −0.311 R | 14.240 R |
| **held-out, `max_er=0.013`** | 197 | 62.9% | −0.219 R | 43.718 R |

**Does NOT pass this project's predefined acceptance bar.** This project has consistently required
BOTH companion metrics (expectancy AND max drawdown — Phase 8 Step 1 fixed this pairing explicitly,
and Step 1 of this effort selected `0.013` over other plateau points partly *because* it improved
both together on the training window) to hold out-of-sample, not just one:
- **Expectancy direction holds, does not reverse**: held-out expectancy still beats held-out
  baseline (−0.219 R vs. −0.311 R, a genuine 0.092 R improvement) — unlike `0.2`, which reversed
  into a result worse than doing nothing. On this metric alone, `0.013` looks better than `0.2`.
- **Drawdown reverses badly**: the training window showed drawdown *improving* under this filter
  (37.120 R → 22.960 R, a 38% reduction — part of why this looked like a genuine risk-reduction
  candidate). Held-out, it does the opposite: drawdown roughly **triples** versus the held-out
  baseline (14.240 R → 43.718 R, a 207% increase) and is also nearly double the training window's
  own drawdown under this same filter. Trade count also explains why: the filter frees up far more
  exposure-cap headroom on this window than on the training window (45 → 197, a ~4.4x increase,
  versus training's ~2x increase), clustering more trades and compounding risk exactly where this
  filter's original purpose was to reduce it.
- Since the filter's stated motivation (Motivation section, above) is explicitly damage limitation
  / risk reduction, not just expectancy — a candidate whose out-of-sample drawdown gets
  *dramatically worse* than doing nothing fails on its own terms, even though its expectancy number
  looks superficially better than `0.2`'s outright reversal.

**Verdict: `max_entry_efficiency_ratio=0.013` is REJECTED, same as `0.2`, on the drawdown leg of
validation.** Not adopted. No production default changed — `GridStrategyConfig` still defaults to
`max_entry_efficiency_ratio=None` everywhere.

```
pytest -q                        -> 428 passed (unchanged -- only a new read-only script added)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this entry**:
`scripts/run_demo_execution_backtest_regime_filter_test_window_validation_0013.py` (new), this
checkpoint doc.

## Effort closed — negative result, user-approved

User explicitly chose to close this effort out as a negative result rather than attempt a third
Step 1 restart. **Final outcome: no viable grid regime filter threshold was found.** Both
candidates this effort produced were tried in good faith through the full Step 1→3 discipline and
both failed out-of-sample validation, for two genuinely different reasons — not the same bug
recurring twice:
- `max_entry_efficiency_ratio=0.2`: training-window expectancy improvement (−0.302 R → −0.209 R)
  reversed outright out-of-sample (−0.311 R → −0.361 R, worse than doing nothing).
- `max_entry_efficiency_ratio=0.013`: training-window expectancy improvement held directionally
  out-of-sample (−0.311 R → −0.219 R, a real gain), but max drawdown — the required companion risk
  metric this project has used since Phase 8 Step 1 — reversed badly (14.240 R → 43.718 R, nearly
  tripling), driven by the filter freeing far more exposure-cap headroom on the held-out window
  than it did on the training window (45 → 197 trades, vs. ~2x on training).

Two independent, differently-shaped candidates both failing out-of-sample — one via expectancy
reversal, the other via drawdown reversal — is itself informative: it's evidence against a stable,
generalizable Efficiency-Ratio threshold existing for this grid/symbol/window combination at all,
not just evidence that these two specific numbers were wrong picks. Grid's underlying negative
expectancy (Phase 8 Step 7) remains real and diagnosed (disproportionately trend-concentrated),
but a regime *filter* built on Kaufman's Efficiency Ratio has not been shown to fix or meaningfully
mitigate it on the evidence gathered so far.

**No production code changed anywhere in this effort, start to finish.**
`GridStrategyConfig.max_entry_efficiency_ratio`/`efficiency_ratio_period` (added in Step 2) remain
in the codebase, both still opt-in and default-`None`/`14` — dead but harmless: zero behavior
change for any caller that doesn't explicitly opt in, fully unit/integration tested, and available
unchanged if a future effort wants to try a different threshold-selection approach against this
same mechanism rather than rebuilding it. Left in place rather than reverted, since removing
tested, harmless, opt-in infrastructure isn't itself risk-reducing and this project's own
conventions elsewhere (e.g. the backtest engine's parallel simulation hook, kept "available for any
future threshold re-sweep") treat this as normal.

**Re-opening this effort in the future** would need a genuinely new idea, not a third blind
threshold search over the same signal/window combination — e.g. a different regime signal
entirely, a different windowing/split strategy, or accepting that grid's entry-timing problem
(Phase 8's own diagnosis: "an entry-timing quality problem, not an SL/TP-shape problem") may not be
addressable by *any* single-bar-window filter of this shape. That's a future, separately-scoped,
separately-approved decision — not implied or pre-committed by this closure.

## Exact next smallest task

None — this effort is closed. Any further grid regime-filter work is a fresh, separately-scoped
effort requiring its own explicit proposal and approval, per this project's normal workflow
(`AGENTS.md`'s "Required workflow"). Grid's default configuration is unchanged; the production
default remains what Phase 8 left it (see `AGENTS.md`'s "Grid regime filter" progress entry for
the one-line summary).

**Continuation prompt for a new session** (only needed if this effort is ever re-opened): "Read
AGENTS.md and docs/GRID_REGIME_FILTER_CHECKPOINT.md. This effort is CLOSED as a negative result —
both candidates it produced (max_entry_efficiency_ratio=0.2 and 0.013) were rejected by
out-of-sample testing, for two different reasons (expectancy reversal and drawdown reversal
respectively). No production default changed. Re-opening this needs a genuinely new idea, not a
repeat threshold search over the same signal — read the 'Effort closed' section for the reasoning
before proposing anything."

## Status

**CLOSED — negative result, user-approved 2026-08-06.** First candidate
(`max_entry_efficiency_ratio=0.2`): REJECTED, did not validate out-of-sample (expectancy
reversed). Second candidate (`max_entry_efficiency_ratio=0.013`), found via a fresh Step 1 (wide
sweep + fine-grained probe): REJECTED, did not validate out-of-sample (expectancy held, but max
drawdown reversed badly). No production default changed anywhere in this effort —
`GridStrategyConfig.max_entry_efficiency_ratio` still defaults to `None` everywhere. No further
work planned; re-opening requires a new proposal.
