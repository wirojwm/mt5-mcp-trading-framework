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

## Remaining risks / not done

- `GridCycleError`'s consumers: nothing in this codebase currently calls `run_grid_cycle()`
  from a long-running/scheduled context (no such orchestration script exists yet — out of
  scope, see `AGENTS.md`'s "pipeline wiring" note). Whoever eventually does needs to actually
  catch `GridCycleError` and decide what to do with `.completed_results`/`.errors` — this phase
  only guarantees the information is available, not that anything downstream consumes it yet.
- `StateStore`'s O(N) per-write / O(N²)-for-N-writes scaling (found this step, see above) is
  unaddressed — fine at current usage, a real risk if ticket volume ever grows large under
  sustained autonomous use.
- No live/MCP-adjacent failure testing (e.g. actually killing the MCP subprocess mid-call) —
  everything in this phase is pure/mock-based, no live call was made or needed.

## Exact next smallest task

Both scoped slices of this phase are done (grid_cycle failure handling + state-store-at-scale
sweep). Ask the user whether to continue Phase 7 further (e.g. live/MCP-adjacent failure
testing, or addressing the StateStore scaling finding), or consider this phase sufficient for
now and move to pipeline wiring (a separate, later-approved effort per `AGENTS.md`) or
something else. Not started — stopping here per this project's standard "explain, implement,
report, stop for approval" workflow.
