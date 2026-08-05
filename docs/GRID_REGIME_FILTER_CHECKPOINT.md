# Checkpoint: Grid regime filter — proposal, not yet started

A new, separately-scoped effort, not one of this project's numbered phases (0–7) and not part of
Phase 8 itself — same relationship "wire real adapters" had to Phase 6, or "pipeline wiring" had
to Phases 6/7: motivated by a Phase 8 finding, but its own explicitly-approved unit of work, per
`AGENTS.md`'s required workflow ("explain the goal, list files to create/change, identify risks
and assumptions — before editing... stop and wait for explicit approval before starting the next
phase"). **Nothing in this doc has been built. This is the proposal, for review before any code.**

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

## Status

**Not started.** Awaiting a decision on the open design points above (or explicit approval of the
provisional choices) before Step 1's threshold sweep is run.
