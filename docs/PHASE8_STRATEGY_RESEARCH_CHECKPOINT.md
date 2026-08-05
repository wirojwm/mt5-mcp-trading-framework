# Checkpoint: Phase 8 — strategy research, edge validation, parameter tuning

Handoff doc for continuing this phase in a new session. Read `AGENTS.md` first for overall
project context, specifically the "Forward phases" section — Phase 8 is not one of this
project's original numbered phases (0–7); it's the user's own forward framing, formalized here
per the roadmap review that preceded this doc. Read `docs/PIPELINE_WIRING_CHECKPOINT.md`'s
final entries too: Phase 8 was explicitly gated on Phase 7's live/MCP-adjacent failure-testing
gap being closed or accepted, which it now is (Stage 3 Parts 1–2 live-verified, Part 3
explicitly accepted as an open risk).

## Goal

Unlike phases 0–7 (phases of *building* this codebase), Phase 8 is a phase of *running and
tuning* the strategy once built: does the grid/runner combination, as currently implemented,
have a real, positive, cost-net edge — and if any of its parameters should change, which ones
and to what, validated on data the tuning process never saw. No live trading is involved. This
phase is 100% research against historical market data; nothing here changes what a real cycle
does unless a later, separately-approved step decides to act on a finding.

## Scoping pass (before any code written) — read this before continuing

Grounded in the actual codebase, not designed from the phase name alone. Read
`strategy/grid.py`, `strategy/runner.py`, `strategy/guard.py`, `sizing/money.py`,
`risk/portfolio_guards.py`, `mt5_adapter/mcp_market_data.py`, and `domain/models.py`, and
grepped the whole `pipeline/`/`scripts/` tree for any existing backtest/tuning infrastructure,
before writing anything below.

**Tunable parameter surface today**: `GridStrategyConfig` (6 fields: `atr_period`,
`center_ema_period`, `center_mode`, `step_mult`, `min_step_points`, `sl_atr_mult`),
`RunnerStrategyConfig` (7 fields: `fast`, `slow`, `min_bars_floor`, `atr_period`, `sl_atr_mult`,
`tp_atr_mult`, `min_stop_distance_points`), `MoneyConfig` (7 fields, 3 lot-sizing modes:
`fixed`/`atr_scale`/`risk_percent`), `ExposureCaps` (`max_open_lots`, `budget_max_lots`). Several
current defaults are placeholders by their own module docstrings' admission — grid's
`sl_atr_mult=2.0` and every SL/TP field on `RunnerStrategyConfig` are documented as
"new, project-original, no legacy value, open to tuning," not validated choices.

**Guard (`strategy/guard.py`, `evaluate_guard()`) is unwired — confirmed by grep, and
DECIDED OUT OF SCOPE for this phase.** It's a real, ported, unit-tested EMA-based
partial-close/flatten exit rule, but it is never called anywhere in `pipeline/` or any live
script — every real cycle this project has ever run, including every live loop run, has
operated with no active exit-management logic beyond the static SL/TP set at order placement.
**User's explicit decision**: Phase 8 tunes and validates grid/runner exactly as they run
today, with no exit-guard, rather than wiring the guard in first. Reasoning this implies (not
independently re-litigated here, just recorded so a future session doesn't re-open it by
accident): wiring the guard into the pipeline is itself a pipeline-completeness change, closer
in kind to the "pipeline wiring" effort than to tuning already-deployed behavior — folding it in
here would mean Phase 8 spends its early budget on a wiring task instead of research, and would
make every backtest result depend on a not-yet-live-verified new pipeline behavior. Revisit
whether to wire the guard as a *separate*, explicitly-scoped effort — before or after Phase 8,
either order is legitimate, but not silently inside it.

**No backtest, tuning, or walk-forward infrastructure exists anywhere in this codebase.** Fully
greenfield — confirmed, not assumed.

**No cached historical market data exists anywhere in this repo.** Every prior real
market-data read in this project's history (dry-run pipeline, every live cycle) has been a live
pull for immediate use, never persisted.

**No Phase 0 legacy-audit document survives** with recorded legacy strategy performance —
`AGENTS.md` notes Phase 0 "produced no code, by nature." Phase 8 establishes a new performance
baseline; it does not validate against a known legacy track record, because none exists in
writing.

**Cost data available today**: `MarketBar`/`SymbolInfo` both carry `spread` (points). No
commission/swap field exists anywhere in `domain/models.py`. Whether one is obtainable via any
MCP tool (e.g. a deal-history tool) is an open research question — not yet checked, first item
of Step 3 below.

## Open questions, decided or still open

1. **Guard wiring — DECIDED**: out of scope for Phase 8 (see above).
2. **What "edge" means, concretely — DECIDED (Step 1)**: **per-trade expectancy in R-multiples,
   net of transaction costs, computed separately per strategy** (grid and runner each must
   independently clear the bar — never blended, since blending could hide one strategy's losing
   edge behind the other's positive one). R = the strategy's own actual per-trade risk distance
   at the time of that trade (`|entry_price − sl_price|`, using the real, already-computed SL each
   strategy attaches — grid's ATR-scaled `sl_price`, runner's `compute_stop_distances()` output —
   not a fixed nominal value); a trade's result is expressed as its P&L divided by R, and expected
   R is chosen over profit factor/Sharpe/CAGR specifically because it normalizes across grid's and
   runner's very different stop widths, which none of those alternatives do (recorded reasoning,
   not just the conclusion, in case a later session needs to revisit this). **Required companion
   metric, not merely informative**: max drawdown, expressed in the same R units (peak-to-trough
   equity decline) — expectancy alone says nothing about the ride, and a strategy with positive
   expectancy but a catastrophic drawdown path is not validated by this metric alone. **Required
   minimum sample size**: no "edge validated" claim (positive or negative) is made below **30
   trades per strategy** in the tested window — expectancy from fewer trades is statistically too
   noisy to trust either way; 50+ preferred before treating a positive result as reasonably
   reliable, not just a hard floor at 30. Cost inputs (spread now, commission/swap if Step 3 finds
   them available) feed directly into each trade's P&L before it's divided by R — this metric's
   definition doesn't change once Step 3 resolves question 4 below, only its cost inputs get more
   complete.
3. **Data source — OPEN, but direction is clear**: a local historical-bar cache, seeded by one
   read-only live pull per symbol/timeframe (same risk category as the MCP disconnect effort's
   read-only calls — no order, no `executor`, no credential exposure beyond what any other real
   script already does). How much real history the demo terminal actually retains for BTCUSD is
   unknown until checked — this is Step 2 below, deliberately sequenced before any engine design.
4. **Cost-model completeness — OPEN**: is commission/swap available anywhere, or is spread-only
   the honest ceiling of what this project can model? Step 3 below.

## Proposed sub-steps, smallest/lowest-risk first

Numbered as steps within this doc (matching every other checkpoint doc's convention), not as
sub-phases with their own numbers — Phase 8 is one phase, tracked here.

| Step | Scope | Entry criteria | Exit criteria | Key risk |
|---|---|---|---|---|
| 1 | Decide the edge metric (question 2) | This doc reviewed | A single, written target metric | None — pure decision — **DONE**, see question 2 above |
| 2 | Historical data acquisition + local cache | Step 1 done | Reusable, tested loader; real data actually pulled and cached for ≥1 symbol/timeframe, live-verified once | MT5 may retain less history than hoped — discover this **first**, before designing anything around an assumed depth |
| 3 | Cost-model research (question 4) + pure backtest/replay engine, reusing existing strategy/risk/order_planning code untouched | Step 2 has real data | Deterministic trade log + equity curve from real historical data, reviewed before being treated as meaningful | **Look-ahead bias** — the single most common, most dangerous backtest-engine bug class; needs explicit fill-logic tests proving no future bar ever influences a past decision, not just informal review |
| 4 | Transaction-cost / stress modeling | Step 3 engine trusted | Sensitivity table (expectancy at 1x/2x/5x observed spread) | Cost model may be incomplete per question 4's answer |
| 5 | Parameter tuning | Steps 3–4 done | A justified parameter set (or explicit "no change needed"), sweep results archived, not just a black-box "best" number | **Overfitting to one historical window** — the single biggest intellectual risk of this whole phase; requires a train/test split, never tuning against the same window used for Step 6 |
| 6 | Walk-forward / out-of-sample validation | Step 5 produces a candidate | Honest report of the *locked* candidate's performance on a disjoint, later window never used in Step 5 — a disappointing result here is a valid, useful phase outcome, not a failed phase | None beyond Step 5's — this step exists specifically to catch Step 5 overfitting |
| 7 (optional) | Regime analysis | Only if Steps 5–6 show regime-dependence worth investigating | Informs whether a regime filter is worth a future, separate proposal | Lowest priority — may be dropped entirely if 5–6 already show robustness across the tested window |

## Explicitly not in Phase 8

- No live/demo order execution changes of any kind.
- No production strategy-code change unless Steps 5–6 justify one — and even then, making that
  change is its own separate, explicitly-approved step, not automatic.
- Does not wire `evaluate_guard()` into the pipeline (see decision above).
- Does not touch Phase 9 or live-pilot readiness criteria.
- Does not require or assume any particular symbol beyond what's already traded (`BTCUSD`) —
  expanding to `EURUSD`/`XAUUSD` (named in the live-pilot framing) is Phase 9/live-pilot
  territory, not decided or needed here.

## Step 2, first action — historical data depth probe: live-verified, real BTCUSD history goes back to 2015

Ran `scripts/run_demo_execution_historical_data_probe.py` (new, read-only — only calls
`get_candles_latest` via `McpMarketDataSource.get_bars()`, no `executor` reference anywhere in
the file, same pattern as `scripts/run_demo_execution_mcp_disconnect_smoke_test.py`). Requested
50,000 bars per timeframe (deliberately far more than expected to be cached) for `M1`, `M5`,
`M15`, `H1`, `H4`, `D1`, since `get_candles_latest` wraps `MetaTrader5.copy_rates_from_pos()`
directly (confirmed by reading the vendored client) — there's no hard-coded count cap anywhere
in this project's or the vendored client's code; requesting more than is cached just returns
fewer bars, never errors or blocks on a broker download, so a single large request per timeframe
safely reveals the real ceiling (or shows the request itself was the bottleneck, see below).

**Result, real data, all six timeframes**:

| Timeframe | Bars returned | Earliest | Latest | Span |
|---|---|---|---|---|
| M1 | 50,000 (capped by request) | 2026-07-01 | 2026-08-05 | 35.2 days |
| M5 | 50,000 (capped by request) | 2026-02-10 | 2026-08-05 | 175.7 days |
| M15 | 50,000 (capped by request) | 2025-02-19 | 2026-08-05 | 531.9 days |
| H1 | 50,000 (capped by request) | 2020-07-22 | 2026-08-05 | 2204.5 days |
| H4 | 16,112 (natural limit) | 2015-01-01 | 2026-08-05 | 4234.2 days |
| D1 | 3,750 (natural limit) | 2015-01-01 | 2026-08-05 | 4234.0 days |

**Interpretation**: `H4`/`D1` weren't capped by the request (16,112/3,750 bars is well under the
50,000 ceiling) — they hit the *terminal's actual earliest cached data*, 2015-01-01, meaning this
demo terminal has real BTCUSD history back over a decade. `M1` through `H1` all returned exactly
50,000 bars, meaning **the request size, not real availability, was the limiting factor for those
four** — more history almost certainly exists at every finer granularity too, not yet measured.
This is a strong, clearly-sufficient result for Step 3 onward: even the already-confirmed depth
(35 days of `M1`, 6 years of `H1`) comfortably supports a real train/test split with room to
spare for the 30-trade-per-strategy minimum sample decided in Step 1, and finding the *exact*
`M1`–`H1` ceiling isn't needed to proceed — only relevant if Step 3's chosen backtest timeframe
later turns out to need more than what's already confirmed available.

No order, no symbol trading call, no `executor` reference — fully read-only. Process cleanup
confirmed clean afterward (no stray processes, verified read-only, same discipline as every
other real script this project runs).

```
pytest -q -> 356 passed (unchanged -- this script lives outside testpaths, never collected)
```

**Files changed this entry**: `scripts/run_demo_execution_historical_data_probe.py` (new), this
checkpoint doc.

## Exact next smallest task

**Step 1 done** (edge metric decided). **Step 2's discovery action done** (real history depth
confirmed, more than sufficient). Step 2's remaining deliverable — a reusable, tested local
cache/loader that actually persists pulled bars (not just probes them) — is next, along with
deciding a storage format (e.g. one file per symbol/timeframe under `var/market_data/`) and
exactly which timeframe(s) Step 3's backtest engine will target first (likely `M1` or `M5`,
matching what grid/runner actually trade on live).

## Step 2, remaining deliverable — local cache/loader built and seeded: Step 2 is now DONE

**Storage format decided**: one CSV file per symbol+timeframe (`backtest/market_data_cache.py`'s
`cache_path()` → `var/market_data/<SYMBOL>_<TIMEFRAME>.csv`), header row
`time,open,high,low,close,tick_volume,spread` — `symbol`/`timeframe` aren't stored per-row, only
implied by the filename, matching `state/store.py`'s per-ticket-file precedent of keeping file
identity in the path. Chose stdlib `csv` over pandas/parquet: `pyproject.toml` has zero hard
runtime dependencies (`dependencies = []`), and `state/store.py` already established this
project's own precedent of plain-stdlib local persistence — no new dependency needed for
something this simple.

**Package**: new `src/mt5_mcp_trading/backtest/` — `market_data_cache.py`
(`cache_path()`/`save_bars()`/`load_bars()`/`merge_bars()`), pure file I/O only, zero
`mt5_adapter`/`mcp_adapter`/`execution` imports anywhere (voluntary, matching the live pipeline's
own pure-core discipline, even though `tests/test_architecture.py`'s `PURE_PACKAGES` list is
specifically about the documented live pipeline and wasn't extended to include `backtest` — a
deliberate choice, not an oversight, since this package sits outside that pipeline entirely).
`save_bars()` raises on an empty list or mixed symbol/timeframe within one call — a real
correctness guard, not padding: a caller accidentally combining two symbols into one file is
exactly the kind of bug this catches for free. `merge_bars()` dedups by timestamp (new bar wins
on a re-fetch) so re-running the seed script later only adds what's actually new, never
duplicates or silently loses previously-cached bars. **10 new unit tests**
(`tests/unit/test_backtest_market_data_cache.py`): cold-start empty-cache behavior, exact
round-trip, sort-on-write, both validation-error paths, merge dedup/sort, and the realistic
incremental-extension flow (seed once, then merge a second overlapping fetch) end to end.

**Timeframe decided**: `M1` only, for now — matches `TIMEFRAME="M1"`, the only timeframe any real
pipeline script in this project actually trades on live (both grid and runner, confirmed by
reading `scripts/run_demo_execution_pipeline_cycle.py`/`pipeline_loop.py`). Other timeframes can
be added later if Step 7 (regime analysis) ever needs them — not needed to proceed.

**Seeded for real**: `scripts/run_demo_execution_historical_data_cache_seed.py` (new, read-only —
only calls `get_candles_latest` via `McpMarketDataSource.get_bars()`, no `executor` reference
anywhere in the file, same pattern as every other real-connection script this project has built).
Run once, live: fetched 50,000 `BTCUSD` `M1` bars (`2026-07-01T01:59` → `2026-08-05T06:48`,
matching the probe's earlier finding), merged against an empty starting cache, wrote
`var/market_data/BTCUSD_M1.csv` — confirmed on disk: 50,001 lines (header + 50,000 rows), 3.5 MB,
spot-checked first/last rows match the script's own reported range exactly. `var/` is
git-ignored, so this cache is a local, machine-specific artifact, never committed.

No order, no symbol trading call — fully read-only. Process cleanup confirmed clean afterward
(no stray processes, read-only-verified).

```
pytest -q                        -> 366 passed (356 previously + 10 new)
pytest tests/test_architecture.py -q -> 13 passed (unaffected -- backtest/ isn't in PURE_PACKAGES)
```

**Step 2 is now fully done**: discovery action (real history depth confirmed) and the remaining
deliverable (a real, tested, live-seeded local cache) are both complete.

**Files changed this entry**: `src/mt5_mcp_trading/backtest/__init__.py` (new),
`src/mt5_mcp_trading/backtest/market_data_cache.py` (new),
`tests/unit/test_backtest_market_data_cache.py` (new),
`scripts/run_demo_execution_historical_data_cache_seed.py` (new), this checkpoint doc.
`var/market_data/BTCUSD_M1.csv` was also created but is git-ignored, never committed.

## Step 3 — the backtest/replay engine: built, tested, run against real data, and a real cadence bug found and fixed along the way

Scoped in conversation first (see the design notes this section assumes): reuse
`run_grid_cycle()`/`run_runner_cycle()` completely unmodified by driving three backtest-flavored
implementations of this project's own `MarketDataSource`/`AccountReader`/`OrderExecutor`
Protocols — the same seam `DryRunExecutor`/`MockMarketDataSource`/`MockAccountReader` already
exploit for dry-run testing, so no strategy/risk/order_planning logic is reimplemented anywhere.

**Cost-model research (question 4), decided**: `get_deals` exposes real `commission`/`swap`/`fee`
fields (confirmed by reading the vendored `metatrader_client` source — it returns MT5's raw
`TradeDeal` struct directly), but only for this account's own real historical deals, not usable
as a per-bar backtest input. **Decision: spread-only cost modeling**, using each cached bar's own
real historical `spread` field — the practical, evidence-consistent default for this account's
broker profile (retail CFD/crypto demo, no separate commission structure exposed anywhere).

**Built**: `src/mt5_mcp_trading/backtest/ledger.py` (`BacktestLedger` — open positions, pending
orders, closed trades, all in-memory; `ClosedTrade.r_multiple` computed directly from each
trade's own risk distance, never a fixed nominal value), `engine.py` (`ReplayCursor` — the one
look-ahead-bias control point; `BacktestMarketDataSource`/`BacktestAccountReader`/
`BacktestOrderExecutor`; `run_backtest()`, the replay loop), `metrics.py`
(`expectancy_r()`/`max_drawdown_r()`/`has_minimum_sample()`, implementing Step 1's decided
metric directly). **31 new tests** across three files — ledger mechanics (R-multiple math for
both BUY/SELL, hand-verified), metrics (a hand-computed drawdown example), and engine mechanics:
a dedicated look-ahead test (`visible_bars()` never returns anything past the cursor even when
asked for far more than exists), fill-timing tests for both order types, and — found directly by
testing, not assumed safe — **a real bug**: a position that fills mid-call was also being
exit-checked in that same call, so a same-bar coincidence between a fill price and its own SL/TP
could close a position in the identical call it opened in, violating this engine's own documented
"no same-bar exit" rule. Fixed by tracking `just_filled` tickets within each
`check_fills_and_exits()` call and excluding them from that call's own exit pass. A separate test
explicitly demonstrates *why* the loop's ordering matters (`check_fills_and_exits()` is
intentionally stateless/order-agnostic — it would happily re-fill/re-exit the same bar if called
twice; the "no same-bar" guarantee is entirely `run_backtest()`'s own loop discipline, not
something the function enforces on its own).

**Convention locked in, as scoped**: same-bar SL/TP double-hit assumes SL first (conservative);
drawdown reported in R-units, not $ (no calibrated contract-size/pip-value figure exists anywhere
in this codebase's domain model, and Step 1's primary metric doesn't need one either).

```
pytest -q                        -> 397 passed (366 previously + 31 new)
pytest tests/test_architecture.py -q -> 13 passed (backtest/ deliberately not added to PURE_PACKAGES)
```

### Real-data demonstration run — a real cadence bug found and fixed

`scripts/run_demo_execution_backtest_btcusd_m1.py` (new): makes exactly one real, read-only MCP
call (`get_symbol_info(BTCUSD)`, real broker constraints rather than a hardcoded guess —
`volume_max=5.0`, `freeze_level=0`, `filling_modes=('FOK',)`, notably different from values used
in earlier synthetic test fixtures elsewhere in this project, confirming the "pull it live"
decision was the right call), then runs `run_backtest()` entirely offline against the real cached
50,000-bar `BTCUSD` `M1` history from Step 2.

**First run: 27,269 total closed trades (27,234 of them `runner`'s) over 50,000 bars — an
obviously implausible rate, investigated before trusting it.** Traced to a real design gap in the
engine, not the strategy: `run_backtest()` evaluated a new cycle on *every single bar*, but the
only real deployment this project has ever actually run
(`scripts/run_demo_execution_pipeline_loop.py`) evaluates a cycle once every
`CYCLE_INTERVAL_SECONDS=300` (5 minutes) regardless of the `M1` bar timeframe strategies compute
signals from — confirmed by reading that script directly. The engine was evaluating 5x more often
than any real deployment ever has. **Fixed**: added `cycle_interval_bars` to `run_backtest()`
(default `1`, for engine-mechanics testing; real demonstration runs should pass `5` for `M1` data
to match the real loop's cadence) — `check_fills_and_exits()` still runs every bar (real SL/TP
monitoring is continuous), only *new*-order evaluation is throttled. +3 tests, including one
proving the throttle has a measurable, directional effect (not just that the parameter exists).

**Second run, with `cycle_interval_bars=5`: 9,924 total closed trades (9,881 `runner`'s) — better,
still high, and traced to a second real thing, this time a genuine strategy property, not an
engine bug**: `run_runner_cycle()`'s own module docstring already says it plainly — "No
duplicate-order check here... that guard is about pending orders sitting at a price, and runner
produces price-less MARKET orders." The exposure cap (`max_open_lots=0.06` / `fixed_lot=0.01` = 6
concurrent slots) is the *only* thing limiting runner's exposure — nothing stops it from
re-entering the same direction on its very next evaluation the moment any slot frees up, as long
as the MACD sign hasn't flipped. No live run before this has ever run long enough (max 12 cycles)
to expose this; a 10,000-cycle backtest does, immediately.

**Real results, both strategies, this cached window (`2026-07-01` → `2026-08-05`, ~35 days),
current default parameters, `cycle_interval_bars=5`**:

| Strategy | Closed trades | Win rate | Expectancy | Max drawdown |
|---|---|---|---|---|
| grid (magic 71101) | 43 | 55.8% | **−0.308 R** | 13.72 R |
| runner (magic 72101) | 9,881 | 28.0% | **−0.159 R** | 1,662.0 R |

Both strategies show **negative expectancy** at current default parameters over this window. Both
comfortably clear Step 1's 30-trade minimum sample (43 and 9,881). Grid's smaller, more plausible
trade count reflects its existing duplicate-order protection (a 0.05-price-proximity check);
runner's extreme volume and drawdown are consistent with, and now directly evidence for, the
missing re-entry throttle above — a real, previously-invisible strategy design gap this
backtesting effort exists to surface, not a backtest artifact.

**Explicitly not resolved by this step, and not attempted here**: whether these negative numbers
reflect a genuinely bad default parameter set (Step 5's job), an incomplete cost model (Step 4's),
a real strategy design gap worth fixing (runner's missing re-entry throttle — a production code
change, its own separate, explicitly-approved decision, not made here), or some combination.
**This is the first real read, not a verdict** — exactly the caution Step 3's own exit criteria
called for ("reviewed before being treated as meaningful"). No production strategy code was
touched this step.

No order, no symbol trading call beyond the one `get_symbol_info` read — fully read-only. Process
cleanup confirmed clean after both live runs.

```
pytest -q                        -> 397 passed (unchanged after the cadence fix's own +3 tests were already counted)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this entry**: `src/mt5_mcp_trading/backtest/ledger.py` (new),
`src/mt5_mcp_trading/backtest/engine.py` (new), `src/mt5_mcp_trading/backtest/metrics.py` (new),
`tests/unit/test_backtest_ledger.py` (new), `tests/unit/test_backtest_metrics.py` (new),
`tests/unit/test_backtest_engine.py` (new),
`scripts/run_demo_execution_backtest_btcusd_m1.py` (new), this checkpoint doc.

## Fix runner's re-entry throttle — production code change, decided and shipped

User decided: fix the gap now, before Step 4/5, rather than tune parameters on top of it. This is
the first production-code change of Phase 8 — everything before this was research/backtest-only.

**Design**: at most `RunnerStrategyConfig.max_concurrent_positions` (new field, default `1`) open
runner positions per magic at a time — the simplest, most conservative option considered (vs. a
same-direction-only dedup or a time-based cooldown, both more complex and each still requiring
their own tuning question Step 5 would have to answer anyway). Implemented as a new pure guard,
`risk/symbol_guards.py`'s `check_position_limit(positions, max_concurrent)`, following the exact
existing pattern (`check_duplicate_order`'s neighbor in the same module, combined via
`combine([...])` alongside `check_exposure_cap`, same as every other guard in this codebase — "no
guard here may ever be skippable by another guard passing," per `AGENTS.md`'s safety rules).
Wired into `pipeline/runner_cycle.py` as one more entry in the existing `combine([...])` list —
no new control flow, no special-casing.

**Tests**: +4 unit tests for `check_position_limit()` itself (approved/rejected at/below/above the
limit), +3 integration tests in `test_runner_dry_run_pipeline.py` — the exact regression this fix
targets (an existing open position blocks a new submission even with a deliberately generous
exposure cap, proving the position-count guard is what's blocking it, not exposure), a
higher-limit case (`max_concurrent_positions=2` allows a second position), and an explicit
"0 existing < default limit of 1" boundary check. Confirmed the pre-existing magic=0-quirk
regression test (`test_state_store_recovers_exposure_visibility_despite_broker_magic_zero`) still
passes unchanged — this fix doesn't interact with that bug's fix, since both guards are subject
to the same magic-filter blindness/recovery mechanism identically.

```
pytest -q                        -> 404 passed (397 previously + 7 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Re-ran the real backtest to prove the fix actually helps, not just assume it** —
`run_backtest()` reuses `run_runner_cycle()` completely unmodified, so the fix flowed through
automatically on the next run, same real cached `BTCUSD` `M1` data, same `cycle_interval_bars=5`:

| Strategy | Trades (before → after) | Win rate (before → after) | Expectancy (before → after) | Max drawdown (before → after) |
|---|---|---|---|---|
| grid (magic 71101) | 43 → 43 (unchanged, as expected — this fix never touches grid) | 55.8% → 55.8% | −0.308 R → −0.308 R | 13.72 R → 13.72 R |
| runner (magic 72101) | 9,881 → **4,961** | 28.0% → 27.3% | −0.159 R → **−0.182 R** | 1,662.0 R → **950.0 R** |

**Honest read of this, not oversold**: the fix did exactly what it was designed to do — trade
count roughly halved (a far more plausible frequency) and max drawdown dropped 43% (1,662 R →
950 R), a real, substantial risk-management improvement. It did **not** fix runner's underlying
negative per-trade edge — expectancy is essentially unchanged (very slightly worse, within
noise). That's expected and correct: a re-entry throttle bounds *how much* a losing edge can
compound, it was never going to turn a negative-expectancy strategy positive. Fixing the
per-trade edge itself is squarely Step 4 (cost modeling)/Step 5 (parameter tuning)'s job, still
ahead. Grid's numbers are bit-for-bit identical before/after, confirming this change is exactly
as scoped — it touches nothing outside `runner_cycle.py`/`RunnerStrategyConfig`.

No order, no live/trading call this step — one real read-only `get_symbol_info` call as part of
the backtest re-run, same as before. Process cleanup confirmed clean.

**Files changed this entry**: `src/mt5_mcp_trading/risk/symbol_guards.py` (modified — new
`check_position_limit()`), `src/mt5_mcp_trading/strategy/runner.py` (modified — new
`max_concurrent_positions` field), `src/mt5_mcp_trading/pipeline/runner_cycle.py` (modified —
wired in), `tests/unit/test_risk_symbol_guards.py` (modified, +4),
`tests/integration/test_runner_dry_run_pipeline.py` (modified, +3), this checkpoint doc.

## Step 4 — cost/stress sensitivity: built, tested, run against real data at 1x/2x/5x spread

Scoped in conversation first. Rather than model grid's (indirect, via LIMIT-price normalization)
and runner's (direct, per MARKET fill) spread exposure separately, one shared formula
(`half_spread_price(bar, symbol_info, spread_multiplier)`, replacing two independently-duplicated
copies of the same calculation) is scaled by a single `spread_multiplier` and used everywhere
spread cost enters the engine — a real widened-spread environment would affect the quoted tick
and the actual fill cost identically, since they're the same underlying number.

**Built**: `spread_multiplier: float = 1.0` added to `run_backtest()`, threaded into both
`BacktestMarketDataSource` and `BacktestOrderExecutor` (default unchanged, so every existing test
and Step 3's own numbers are unaffected). +3 tests: the shared helper scales linearly
(1x/2x/5x hand-verified), `get_tick()` honors the multiplier, and a MARKET fill's cost doubles
exactly when the multiplier doubles.

```
pytest -q                        -> 407 passed (404 previously + 3 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Real run**: `scripts/run_demo_execution_backtest_stress_test.py` (new) — one real read-only
`get_symbol_info` call, reused across all three stress levels; each level replays the same real
cached `BTCUSD` `M1` data entirely offline. Real results (`cycle_interval_bars=5`, runner's
re-entry throttle already fixed and in effect):

| spread | grid trades | grid expectancy | grid drawdown | runner trades | runner win rate | runner expectancy | runner drawdown |
|---|---|---|---|---|---|---|---|
| 1x | 43 | −0.308 R | 13.72 R | 4,961 | 27.3% | **−0.182 R** | 950.0 R |
| 2x | 41 | −0.365 R | 15.20 R | 5,669 | 19.2% | **−0.424 R** | 2,420.0 R |
| 5x | 35 | −0.327 R | 11.44 R | 7,888 | 4.9% | **−0.852 R** | 6,718.0 R |

**A clear, interpretable finding, not just numbers**: **grid is only mildly cost-sensitive** —
expectancy stays in a narrow negative band (−0.31 to −0.37 R) regardless of spread level, and
trade count/drawdown don't move dramatically. This makes sense given *how* spread enters grid's
economics: it mainly affects whether `normalize_limit_price()`'s minimum-distance constraint
pushes a proposed price out of range entirely (occasionally blocking a submission, hence the
mild 43→41→35 trade-count decline), not a cost applied to every fill — LIMIT orders fill exactly
at their own specified price. **Grid's negative expectancy is therefore NOT primarily a cost
problem** — something else (entry timing, the ATR-scaled step/TP/SL formula itself) is the
dominant driver, a real, useful prioritization signal for Step 5.

**Runner is severely, monotonically cost-sensitive**: expectancy collapses from −0.182 R to
−0.852 R (4.7x worse) as spread widens from 1x to 5x, win rate collapses from 27.3% to 4.9%, and
both trade count and max drawdown *increase* with wider spread (more, not fewer, stop-outs
cycling through the re-entry throttle's one open slot faster as SL gets hit sooner relative to a
wider entry cost). This strongly implicates runner's SL distance (`sl_atr_mult=1.5` by default)
as too tight relative to realistic execution costs, not just an unlucky parameter choice —
directly actionable for Step 5, which now has real evidence for where to look first.

No order, no live/trading call beyond the one read-only `get_symbol_info` pull. Process cleanup
confirmed clean.

**Files changed this entry**: `src/mt5_mcp_trading/backtest/engine.py` (modified —
`spread_multiplier`, shared `half_spread_price()` helper), `tests/unit/test_backtest_engine.py`
(modified, +3), `scripts/run_demo_execution_backtest_stress_test.py` (new), this checkpoint doc.

## Step 5 (in progress) — cache expanded, train/test split built, sweep run against training window only; STOPPED before winner selection (midday break)

Scoped in conversation first (see prior entry). Session paused for a midday break before this
step's own "pick a winner" decision — deliberately left undone, see "Exact next smallest task"
below.

**Cache expanded, live**: `scripts/run_demo_execution_historical_data_cache_seed.py`'s
`FETCH_COUNT` was the *probe script's* request ceiling (50,000), not this terminal's real depth.
Bisected live (500,000 and 150,000 and 100,000 all returned 0 bars outright — some other
tool/response-size limit, not a graceful depth degradation; 95,000 succeeded) to
`FETCH_COUNT=95,000` without over-searching for the exact byte-level ceiling (not needed).
`var/market_data/BTCUSD_M1.csv` now holds 95,000 real bars, `2026-05-30` → `2026-08-05` (~67
days, nearly double the original 35-day window), confirmed on disk.

**Train/test split built**: `backtest/market_data_cache.py`'s new `split_bars(bars,
train_fraction=0.8)` — splits by time/index position (bars are always time-sorted), never by
trade count or any criterion that could leak information across the boundary. Raises on empty
input or an out-of-range fraction; both sides always non-empty even at extreme fractions. +6
unit tests (80/20 split size, strict time ordering, exact coverage with an awkward bar count,
non-empty at extreme fractions, both error paths).

**Sweep script built and run, training window only**:
`scripts/run_demo_execution_backtest_tuning_sweep.py` — one real `get_symbol_info` call reused
across the whole sweep, everything else offline. One-factor-at-a-time (grid and runner's
exposure guards are per-magic independent, confirmed by reading both cycle functions, so this
isolation is valid, not an approximation). Train: 76,000 bars (`2026-05-30` → `2026-07-22`).
Test: 19,000 bars (`2026-07-22` → `2026-08-05`), **held out, not read by this script at all**.

**Real results, training window only**:

| runner `sl_atr_mult` (`tp_atr_mult`=2x) | trades | win rate | expectancy | max drawdown |
|---|---|---|---|---|
| 1.5 (current default) | 6,820 | 29.3% | −0.122 R | 828.995 R |
| 2.0 | 4,393 | 30.0% | −0.101 R | 445.998 R |
| 2.5 | 3,014 | 30.2% | −0.095 R | 291.999 R |
| **3.0** | 1,989 | 31.2% | **−0.065 R** | **172.000 R** |
| 4.0 | 1,173 | 29.2% | −0.123 R | 157.997 R |

| grid `step_mult` | trades | win rate | expectancy | max drawdown |
|---|---|---|---|---|
| 0.3 | 135 | 65.2% | −0.231 R | 32.060 R |
| 0.4 (current default) | 119 | 56.3% | −0.302 R | 37.120 R |
| 0.5 | 121 | 57.0% | −0.259 R | 32.800 R |
| 0.6 | 117 | 55.6% | −0.244 R | 29.321 R |
| **0.8** | 113 | 51.3% | **−0.240 R** | 28.120 R |

**Not yet interpreted or acted on — deliberately stopped here.** Both sweeps show a monotonic-
looking trend toward less-negative expectancy at the tested extremes (runner: `sl_atr_mult=3.0`
best in this table, though `4.0` reverses the trend, worth checking whether the true optimum
sits between 3.0–4.0; grid: `step_mult=0.8`, the edge of the tested range, meaning an even wider
step hasn't been ruled out). **No winner has been picked. No production default has been
changed.** Both remain exactly as Step 4 left them. Whether this looks like a genuine plateau
(trustworthy) or an edge-of-range artifact (needs a wider candidate range before trusting it) is
this step's own next decision, not made yet.

No order, no live/trading call beyond the one read-only `get_symbol_info` pull (cache expansion)
and the sweep's own one read-only `get_symbol_info` pull. Process cleanup confirmed clean.

```
pytest -q                        -> 413 passed (407 previously + 6 new, split_bars)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this entry**: `scripts/run_demo_execution_historical_data_cache_seed.py`
(modified — `FETCH_COUNT` bumped to 95,000, empty-fetch handled gracefully instead of crashing),
`src/mt5_mcp_trading/backtest/market_data_cache.py` (modified — new `split_bars()`),
`tests/unit/test_backtest_market_data_cache.py` (modified, +6),
`scripts/run_demo_execution_backtest_tuning_sweep.py` (new), this checkpoint doc.
`var/market_data/BTCUSD_M1.csv` also grew (95,000 bars now) but is git-ignored, never committed.

## Step 5 — sweep tables interpreted, grid range widened, both candidates decided

**Runner (`sl_atr_mult`)**: the original 1.5→4.0 table is a genuine interior peak, not an
edge artifact — expectancy improves monotonically from 1.5 (−0.122 R) through 3.0 (**−0.065 R**,
the best point), then reverses sharply at 4.0 (−0.123 R, back below 2.0's result). Win rate and
max drawdown (829 R → 172 R) move the same way. Trade count at 3.0 (1,989) is comfortably above
the 30-trade minimum. **Decided candidate: `sl_atr_mult=3.0`** (`tp_atr_mult` stays at its 2x
ratio, i.e. 6.0). Still net-negative at this setting (−0.065 R) — this candidate reduces the
loss substantially, it does not claim a positive edge.

**Grid (`step_mult`)**: the original 0.3→0.8 table's own note ("best at 0.8, the edge of the
tested range") was a misread — sorted by expectancy, 0.3 (−0.231 R) was already better than 0.8
(−0.240 R); the real open question was the *untested low* edge, not the high one. Widened the
sweep with three new low-end candidates (`GRID_STEP_MULT_CANDIDATES` in
`scripts/run_demo_execution_backtest_tuning_sweep.py` now runs 0.15/0.2/0.25/0.3/0.4/0.5/0.6/0.8,
same training window, same one live `get_symbol_info` call reused across the whole sweep,
runner's rows reproduced bit-for-bit identical to the first pass — confirms determinism).

| step_mult | trades | win rate | expectancy | max drawdown |
|---|---|---|---|---|
| 0.15 | 143 | 67.8% | −0.261 R | 37.721 R |
| 0.2 | 137 | 66.4% | −0.256 R | 35.681 R |
| **0.25** | 135 | 67.4% | **−0.225 R** | 31.100 R |
| 0.3 | 135 | 65.2% | −0.231 R | 32.060 R |
| 0.4 (current default) | 119 | 56.3% | −0.302 R | 37.120 R |
| 0.5 | 121 | 57.0% | −0.259 R | 32.800 R |
| 0.6 | 117 | 55.6% | −0.244 R | 29.321 R |
| 0.8 | 113 | 51.3% | −0.240 R | 28.120 R |

The widened range resolves the ambiguity: expectancy does **not** keep improving below 0.3 — it
reverses again at 0.2/0.15, worse than both 0.25 and 0.3. **0.25 is now a genuine interior peak**,
flanked by tested-worse values on both sides (0.2 and 0.3), the same shape that made runner's 3.0
trustworthy rather than an edge guess. The tight cluster 0.15–0.3 all sit in a similar band (65–68%
win rate, −0.22 to −0.26 R expectancy — plausibly within noise of each other given ~113–143-trade
samples), but all of them are clearly, consistently better than the current default 0.4, which is
the single worst point in the entire tested range on both expectancy and win rate. **Decided
candidate: `step_mult=0.25`** (best point tested, comfortably above the 30-trade minimum at 135
trades, though the small sample means this should be read as "meaningfully better than 0.4," not
as precisely distinguishable from its 0.2/0.3 neighbors).

**Candidate parameter set locked in for Step 6**: `RunnerStrategyConfig(sl_atr_mult=3.0,
tp_atr_mult=6.0)`, `GridStrategyConfig(step_mult=0.25)` — training-window evidence only, no
production default changed yet, no test-window data read by anything in this step.

```
pytest -q                        -> 413 passed (unchanged -- only a sweep-script candidate tuple changed)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/trading call beyond the sweep's one read-only `get_symbol_info` pull (same
call already covered by prior approval for this script). Process cleanup confirmed clean.

**Files changed this entry**: `scripts/run_demo_execution_backtest_tuning_sweep.py` (modified —
`GRID_STEP_MULT_CANDIDATES` widened to include 0.15/0.2/0.25), this checkpoint doc.

## Exact next smallest task

**Step 5 is now fully done**: both tables interpreted, grid's range widened and re-run, a
candidate parameter set decided for each strategy (runner `sl_atr_mult=3.0`, grid
`step_mult=0.25`). **No production default has been changed** — `GridStrategyConfig`/
`RunnerStrategyConfig` still ship with their original defaults; only backtest sweep runs have used
these candidates so far. **Step 6 (walk-forward/out-of-sample validation) has not started. No
test-window data has been touched by anything in this phase to date.** Next smallest step: run the
locked candidate set (and, for comparison, the current defaults) against the held-out 19,000-bar
test window (`2026-07-22` → `2026-08-05`) and report both honestly — a disappointing result there
is a valid, useful phase outcome per this step's own exit criteria, not a failed phase. Requires
its own explicit go-ahead before any test-window code is written or run, same standing rule as
every prior step.

**Live testing remains paused for anything order-related — this entire phase is research-only
and read-only by design, but any further real MCP call still needs its own explicit go-ahead,
same standing rule as every prior step in this project.**

**Continuation prompt for a new session**: "Read AGENTS.md and
docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md (Step 5 is DONE — candidate parameter set decided:
runner sl_atr_mult=3.0/tp_atr_mult=6.0, grid step_mult=0.25, both training-window evidence only,
no production default changed). Confirm git status is clean at the latest commit and no live
process is running, then ask me what to do next — running the locked candidate set against the
held-out test window is Step 6, and needs my explicit go-ahead before any test-window code is
written or run."
