# Checkpoint: Phase 7 — regression and failure testing

Handoff doc for continuing this phase in a new session. Read `AGENTS.md` first for overall
project context. This is the project's overall numbered Phase 7 (per `AGENTS.md`'s phase list:
"0: legacy audit, 1: design, 2: foundation, 3: read-only MCP integration, 4: strategy
migration, 5: dry-run pipeline, 6: controlled demo execution, 7: regression and failure
testing") — NOT to be confused with the Phase-6-internal "Step 7" tracked in
`docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md` (MARKET SELL-side live test, already
complete). Two different sequences that happen to share a number.

## Goal

No live trading involved. This phase hardens and tests the existing system's behavior under
failure conditions (dropped connections, adapter exceptions mid-cycle) and adds regression
coverage for behavior that spans more than one call (e.g. guards working correctly across
repeated, chained cycle invocations, not just within a single call).

## Scoping pass (before any code changed)

304 existing tests already carry substantial regression coverage, distributed per-module
rather than centralized — e.g. `test_sizing_money.py`/`test_strategy_grid.py` have explicit
"matches legacy formula" locked-value tests, and `test_mt5_adapter_mcp_order_executor.py`
already has dedicated retcode-trust regression tests. This phase does not re-do that.

Gap found: `run_grid_cycle`/`run_runner_cycle` (`pipeline/`) had **no failure-mode tests at
all**. Neither function caught anything from `market_data`/`account`/`executor` — any
exception from a live adapter call mid-cycle propagated straight through, unhandled.
Concretely, in `run_grid_cycle`: if the BUY side's `executor.submit()` succeeded but the SELL
side's then raised, the function raised without ever returning the BUY side's
`ExecutionResult` — the caller lost that return value entirely (not the real state, since
`McpOrderExecutor` already persists to `StateStore` before returning — only the in-memory
return value).

## Step 1 — design decision: grid_cycle's partial-failure behavior

**Decision**: BUY and SELL are logically independent proposals (different prices, become
different tickets once submitted). One side's `executor.submit()` raising must never silently
prevent the other, healthy side from being attempted, and must never silently discard a result
already obtained for the other side. At the same time, a failure must never be swallowed —
the caller must always find out, loudly, that something went wrong.

**Implemented** (`src/mt5_mcp_trading/pipeline/grid_cycle.py`):
- New `GridCycleError(RuntimeError)`: carries `.completed_results: list[ExecutionResult]`
  (every result actually obtained) and `.errors: list[tuple[str, Exception]]` (per-side
  failures, as `(side, exception)` pairs).
- The per-side loop now wraps `executor.submit()` in `try`/`except Exception`, logs via
  `_logger.exception(...)`, and continues to the next side regardless. After both sides are
  attempted, `GridCycleError` is raised if `errors` is non-empty — carrying everything that
  succeeded alongside everything that failed. Nothing observed during the cycle is silently
  lost; the function still always raises when something went wrong, never returns a
  falsely-clean partial list.
- Read-stage failures (`get_bars`/`get_symbol_info`/`get_tick`/`get_positions`/`get_orders`,
  all before the per-side loop) are deliberately NOT wrapped — there's nothing to preserve yet
  at that point, so the raw exception propagates unchanged. Confirmed by test, not assumed.
- `run_runner_cycle` needed no code change: it makes at most one `submit()` call, so there is
  no "partial results to preserve" scenario — a raise there already propagated correctly.
  Confirmed by test, not assumed.

## Step 2 — failure-injection tests

`tests/integration/test_grid_dry_run_pipeline.py` (+9 tests, +3 test-double helper classes:
`_RaisingOnSideExecutor`, `_RaisingMarketDataSource`, `_RaisingAccountReader`):
- `test_sell_side_failure_preserves_buy_sides_result` / `test_buy_side_failure_still_lets_sell_side_be_attempted`
  — both directions of the core fix: one side raising, the other still attempted and its
  result preserved in `GridCycleError.completed_results`.
- `test_both_sides_failing_reports_both_errors_and_zero_completed`.
- `test_market_data_read_failure_propagates_raw_not_wrapped` (parametrized: `get_bars`,
  `get_tick`, `get_symbol_info`) / `test_account_read_failure_propagates_raw_not_wrapped`
  (parametrized: `get_positions`, `get_orders`) — read-stage failures are NOT wrapped in
  `GridCycleError`, confirmed for every read call, not just one.

`tests/integration/test_runner_dry_run_pipeline.py` (+4 tests, +2 test-double helper classes:
`_RaisingMarketDataSource`, `_RaisingExecutor`):
- `test_market_data_read_failure_propagates_raw` (parametrized: `get_bars`, `get_tick`,
  `get_symbol_info`) / `test_executor_submit_failure_propagates_raw` — confirms the "no
  partial-results issue" reasoning above is actually true, not just assumed.

## Step 3 — repeated-cycle regression test

`test_second_cycle_sees_first_cycles_submission_as_a_duplicate` — chains two real
`run_grid_cycle()` calls: cycle 1 submits both sides via `DryRunExecutor`; cycle 2 is called
against a fresh `MockAccountReader` seeded with an `OrderState` approximating what a real
account would now show (cycle 1's BUY as a live pending order); cycle 2's BUY is confirmed
correctly blocked as a duplicate, SELL still goes through. Distinct from the existing
`test_duplicate_pending_order_blocks_only_that_side_end_to_end`, which pre-seeds state by hand
within a single call rather than chaining two actual cycle invocations — this proves the guard
works *across* invocations, the way a real repeatedly-run cycle actually would use it.

```
pytest -q                        -> 317 passed (304 previously + 13 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed**: `src/mt5_mcp_trading/pipeline/grid_cycle.py`,
`tests/integration/test_grid_dry_run_pipeline.py`,
`tests/integration/test_runner_dry_run_pipeline.py`, this checkpoint doc.

## Step 4 — state-store-at-scale sweep

`tests/unit/test_state_store.py` (+2 tests, +1 helper `_submit_n()`):
- `test_many_tickets_round_trip_correctly_with_mixed_statuses` — 40 tickets, mixed
  OPEN/OPEN_UNPROTECTED/CANCELLED/CLOSED, full write-then-reload-fresh round trip (simulating a
  process restart), confirms every single ticket survives with the exact expected status and
  `all_open()` returns exactly the OPEN+OPEN_UNPROTECTED subset with no duplicates.
- `test_rapid_sequential_mutations_remain_consistent` — 30 tickets, a rapid burst of
  submit/cancel/close calls against the SAME store instance with no reload in between (the
  realistic pattern for this architecture: no threading/multiprocessing anywhere in this
  codebase's actual usage, so genuine concurrent-access testing was deliberately scoped out as
  not applicable — every call is a sequential `await` within one async event loop). Confirms no
  update is lost to the load-mutate-write cycle under a burst.

`tests/unit/test_state_reconcile.py` (+1 test):
- `test_reconciles_correctly_at_scale_with_a_realistic_mixed_dataset` — 600 total tickets
  across disjoint ranges (200 matched, 150 local_only, 120 unknown-via-positions, 130
  unknown-via-orders), with the expected sets computed independently of `reconcile()`'s own
  logic (disjoint ranges by construction) rather than by trusting the function under test.
  Confirms exact correctness at scale, no duplicates, no missing tickets. `reconcile()` is pure
  set arithmetic with no I/O, so this ran in negligible time.

**Real finding, not just confirmation**: the first version of the `StateStore` tests used 250
and 100 tickets and took **36+ seconds** (vs. this project's normal ~5s full suite). Profiled
and root-caused: every `StateStore` write method does a full `_load()` (reads the ENTIRE file)
+ `_write()` (serializes and `os.replace()`s the ENTIRE file) cycle — so N sequential writes is
O(N) work per write, O(N²) total, and `os.replace()`'s per-call overhead on this machine
dominates at any real N. Reduced to 40/30 tickets (still an order of magnitude beyond the
existing 1-2 ticket tests, still catches the same class of bug) to keep the suite fast — see
the in-file comments. **This is a genuine scaling property of `StateStore` as currently
implemented**, not a test artifact: a real long-running deployment accumulating thousands of
tickets in `var/order_state.json` over weeks/months would see every single write get
progressively slower. Not a problem at current usage volume (a handful of smoke-test tickets
so far), but worth knowing before this state store is ever wired into a long-running
autonomous pipeline (see "pipeline wiring" in `AGENTS.md` — exactly the scenario where ticket
counts would actually grow large over time). A future fix, if ever needed, would likely be
pruning closed/cancelled records past some age, or moving to an append-only/indexed format —
out of scope for this phase, flagging only.

```
pytest -q                        -> 320 passed (317 previously + 3 new)
pytest tests/test_architecture.py -q -> 13 passed
```
Full suite wall time: ~7.4s (up from ~5s baseline — the reduced-scale tests still add ~2.4s,
acceptable for what they prove).

**Files changed this step**: `tests/unit/test_state_store.py`,
`tests/unit/test_state_reconcile.py`, this checkpoint doc.

## Step 5 — StateStore O(N²) write-cost fix

Asked the user to pick a direction (per this project's "design decision" pattern) between three
candidates: per-ticket files, an in-memory cache over the existing single-file format, or
pruning old closed/cancelled records. **Chose per-ticket files.** Rationale for why the other
two don't actually fix the problem: an in-memory cache removes only the *read* half of the
load-serialize-write cycle — the write side still serializes+`os.replace()`s the whole growing
file every call, so total cost stays O(N²), just with a smaller constant. Pruning bounds N in
steady state but doesn't change per-write complexity, and raises an unresolved retention-policy
question for this project's audit-trail intent.

**Implemented** (`src/mt5_mcp_trading/state/store.py`, full rewrite):
- Format changed from one big `var/order_state.json` (a `{"records": {ticket: {...}}}` blob) to
  one file per ticket: `var/order_state/<ticket>.json`. Every write method
  (`record_submission`, `mark_sl_tp_attached`, `record_manual_adoption`, `record_cancelled`,
  `record_closed`) now touches only its own ticket's file — O(1) regardless of how many other
  tickets exist. `lookup(ticket)` is likewise O(1) (reads one file, not the whole store).
  `all_open()` still scans the whole directory, O(N) — the only operation that legitimately
  needs every ticket.
- Same atomicity guarantee as before, just scoped to one file: write to `<ticket>.json.tmp`,
  then `os.replace()` — a crash mid-write leaves the previous version of *that* ticket's file
  intact, never a partially-written one.
- `StateLoadError` semantics preserved and sharpened: a corrupt ticket file raises
  `StateLoadError` from any read/write touching *that* ticket, and from `all_open()` (which must
  still hard-stop on any single bad file, since `McpOrderExecutor._current_posture()` depends on
  it for the BLOCKED-posture gate — confirmed this still works via the corrupted-file tests in
  both `test_state_store.py` and `test_mt5_adapter_mcp_order_executor.py`). New: an unrelated
  ticket is now provably unaffected by another ticket's corruption (`
  test_corrupted_ticket_file_raises_state_load_error_only_for_that_ticket`) — a genuine
  improvement the old single-file format couldn't offer (any corruption there hard-stopped
  every ticket, not just one). Also added a filename/content consistency check (a ticket file
  whose embedded `ticket` field disagrees with its own filename raises `StateLoadError` rather
  than silently trusting one or the other).
- One-time migration script: `scripts/migrate_state_store_to_per_ticket_files.py`. Converts an
  old-format file to the new directory layout, verifies every record round-trips exactly before
  touching anything, then renames the source to `<source>.migrated-bak` (never deletes it). Run
  once against the real `var/order_state.json` (3 records from Phase 6 live smoke tests) —
  verified byte-identical content post-migration, backup preserved at
  `var/order_state.json.migrated-bak`.
- Updated `tests/unit/test_state_store.py` (rewritten for the new format, +3 tests: one-file-
  per-ticket isolation, filename/content consistency check, "writing ticket B never touches
  ticket A's file"), `tests/unit/test_mt5_adapter_mcp_order_executor.py` and
  `tests/unit/test_execution_composition.py` (path fixtures renamed from `order_state.json` to
  `order_state`, now a directory; the 3 corrupted-state-file tests rewritten to corrupt one
  ticket file inside the directory rather than the old single blob), and the three
  `scripts/run_demo_execution_*_smoke_test.py` scripts' `STATE_PATH` constants.
- At-scale tests raised from 40/30 tickets to 200/100 (an order of magnitude beyond what the old
  format could afford to test) — full suite (323 tests) still runs in ~9s.

**Benchmarked, not just asserted fixed**: a standalone script wrote N sequential tickets via
`record_submission()` and measured wall time. Old format (from Step 4's profiling): ~28s total
for 250 tickets, cost visibly growing per write. New format: 100→0.30s (3.0ms/write),
200→0.63s (3.1ms/write), 400→1.28s (3.2ms/write), 800→2.81s (3.5ms/write) — per-write cost flat
as N grows 8x, confirming O(1) per write / O(N) total, not O(N²).

**New finding, not fixed by this step**: `McpOrderExecutor._current_posture()` calls
`all_open()` once per `submit()`/`cancel()`/`close_position()` call, to compute
`ExecutionPosture` before every action — this was true under the old format too. `all_open()` is
O(N) (must read every ticket file), so if ticket volume ever grows very large under sustained
autonomous use, the *executor's* per-action posture check would still cost O(N) per action,
independent of anything fixed in this step (which only addressed `StateStore`'s standalone
write-benchmark cost, the thing Step 4 actually measured and flagged). Not a regression — the
old format had the identical cost here — but worth knowing before assuming this step closes out
every angle of "the StateStore scaling problem." No action taken; flagging only, as this
project's culture requires distinguishing "fixed" from "not yet a real problem."

```
pytest -q                        -> 323 passed (320 previously + 3 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `src/mt5_mcp_trading/state/store.py`,
`scripts/migrate_state_store_to_per_ticket_files.py` (new),
`tests/unit/test_state_store.py`, `tests/unit/test_mt5_adapter_mcp_order_executor.py`,
`tests/unit/test_execution_composition.py`, `scripts/run_demo_execution_smoke_test.py`,
`scripts/run_demo_execution_market_smoke_test.py`,
`scripts/run_demo_execution_close_smoke_test.py`, `var/order_state/*.json` (migrated data),
`var/order_state.json.migrated-bak` (backup of the original), `AGENTS.md`, this checkpoint doc.

## Remaining risks / not done

- `GridCycleError`'s consumers: nothing in this codebase currently calls `run_grid_cycle()`
  from a long-running/scheduled context (no such orchestration script exists yet — out of
  scope, see `AGENTS.md`'s "pipeline wiring" note). Whoever eventually does needs to actually
  catch `GridCycleError` and decide what to do with `.completed_results`/`.errors` — this phase
  only guarantees the information is available, not that anything downstream consumes it yet.
- `StateStore`'s per-write O(N²) scaling (found Step 4) is fixed (Step 5, above). The separate,
  not-yet-a-real-problem finding from Step 5 — `McpOrderExecutor._current_posture()`'s
  `all_open()` call being O(N) per submit/cancel/close action — is unaddressed; only worth
  revisiting if ticket volume ever grows very large under sustained autonomous use.
- No live/MCP-adjacent failure testing (e.g. actually killing the MCP subprocess mid-call) —
  everything in this phase is pure/mock-based, no live call was made or needed.

## Exact next smallest task

All three scoped slices of this phase are done (grid_cycle failure handling, state-store-at-
scale sweep, and the StateStore O(N²) write-cost fix). Ask the user whether to continue Phase 7
further (e.g. live/MCP-adjacent failure testing, or the `_current_posture()`/`all_open()` cost
noted above), or consider this phase sufficient for now and move to pipeline wiring (a separate,
later-approved effort per `AGENTS.md`) or something else. Not started — stopping here per this
project's standard "explain, implement, report, stop for approval" workflow.
