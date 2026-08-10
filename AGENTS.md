# Project Instructions

## Goal

Build a clean-architecture MT5 trading system that talks to a MetaTrader 5 MCP server through a
single isolated adapter, safely and incrementally. This project is a rebuild, not a refactor: the
legacy project at `../RealTrade/2509_17_mix_supercross` is read-only reference material only.

## Relationship to the legacy project

- Treat `../RealTrade/2509_17_mix_supercross` as read-only reference. Never edit it from here.
- Do not import from it, depend on it, or copy its architecture wholesale.
- Extract only reusable requirements, business rules, strategy logic, risk controls, execution
  constraints, and lessons learned — deliberately, one component at a time, with tests.

## Required workflow

This project is built in phases (0: legacy audit, 1: design, 2: foundation, 3: read-only MCP
integration, 4: strategy migration, 5: dry-run pipeline, 6: controlled demo execution, 7: regression
and failure testing). For each phase:

1. Explain the goal, list files to create/change, identify risks and assumptions — before editing.
2. Make focused changes.
3. Run tests after every meaningful change.
4. Report: files changed, tests run, remaining risks, next smallest step.
5. Stop and wait for explicit approval before starting the next phase.

Do not skip ahead. Do not broadly refactor already-approved work without being asked.

## Progress

- **Phases 0–5: done.** 0 (legacy audit) and 1 (design) produced no code, by nature. 2
  (foundation), 3 (read-only MCP integration), 4 (strategy migration), and 5 (dry-run pipeline,
  built and tested against mocks only) are all committed.
- **Wire real mt5_adapter/mcp_adapter (post-Phase 5, pre-Phase 6): done.** Not one of the
  numbered phases — a user-requested step to replace the mock `MarketDataSource`/
  `AccountReader` with real implementations before attempting Phase 6. Completed:
  - Real `McpClient`/`McpMarketDataSource`/`McpAccountReader`, unit-tested against a stub
    client and live-verified against the real `metatrader-mcp-server` connection.
  - Real `SymbolInfo` (`get_symbol_info`) and real magic-number filtering
    (`get_positions_with_magic`/`get_pending_orders_with_magic`) — neither tool exists
    upstream; both added locally via `scripts/metatrader_mcp_extended_server.py`, which
    imports the upstream package's `FastMCP` server object unmodified and registers
    additional tools alongside it (not a fork). See `docs/mcp_tool_classification.md` for why
    each was missing and `docs/MCP_ADAPTER_WIRING_CHECKPOINT.md` for the full history.
  - `order_planning.build_order_plan()` and the full `run_grid_cycle`/`run_runner_cycle`
    pipeline live-verified end-to-end against real market data AND real account data together
    (`scripts/run_live_dry_run_pipeline.py`), via `DryRunExecutor` — no order has ever been
    submitted anywhere, real or otherwise.
  - **One open item**: the connected demo account currently has zero positions/pending orders,
    so nothing has yet proven the duplicate-order/exposure-cap guards correctly *discriminate*
    against real, populated, differently magic-tagged data — only that the real adapters
    integrate without error against an empty account. Re-verify once real positions/orders
    exist.
- **Phase 6 (controlled demo execution): in progress, Steps 0–6 done.** `McpOrderExecutor`
  (the first `OrderExecutor` implementation that can place/modify/close a real order) has now
  made real, explicitly-approved live calls on the demo account, each verified against actual
  MT5 state afterward — not just unit-tested. Implemented and proven so far, in small
  individually-tested and individually-approved steps:
  - **Steps 0–3** — Found two blocking safety problems before writing any executor code: (1)
    `require_demo_account()` is unreliable when fed by `McpAccountReader` (the same
    `account_type` inversion bug as elsewhere) — fixed with a second, reliable, env-sourced
    hard gate, `require_demo_account_kind()`. (2) `metatrader_client`'s own `send_order()`
    silently drops `magic` (and forces `comment`, ignores `deviation`/`filling_mode`/`expiry`,
    and — worst — determines success from a terminal-level error code rather than the broker's
    real `retcode`) — deliberately NOT fixed by writing new order-placement code (highest-risk
    code this project could contain); instead the `state/` package tracks intended
    magic/comment/strategy locally, reconciled against real MT5 state by ticket only, with an
    explicit `NORMAL`/`MANAGE_ONLY`/`BLOCKED` posture that refuses new orders (or even
    management of unattributed tickets) whenever local state can't be trusted.
    `McpOrderExecutor` built and unit-tested for LIMIT-order `submit()`/`cancel()`; retcode read
    from the raw response only, never the tool's own success flag. Composition root
    (`execution/composition.py`) is the one place `trading_enabled=True` is ever constructed
    outside a test.
  - **Step 4 — live-proven**: a real LIMIT order was placed and then cancelled on the demo
    account (ticket `171604513`, retcode `10009`/`TRADE_RETCODE_DONE` both times,
    `verified=True` both times via a fresh live read). Along the way, `parse_trade_response()`
    correctly refused to guess when a live response arrived as a positional list rather than
    the assumed dict shape (`MalformedTradeResponseError`, no state written) — fixed by
    accepting both shapes, dict kept only as a fallback.
  - **Step 5 — live-proven**: `close_position()` implemented, unit-tested, and used to close a
    real demo position for real (ticket `171604527`, retcode `10009`, `verified=True`). Live
    attempt #1 was correctly *refused* first — the position had been opened manually outside
    `McpOrderExecutor`, so reconciliation classified it `unknown_real` → `MANAGE_ONLY`, which by
    design blocks unattributed tickets — proving that safety path works, not just the happy
    path. An explicit, narrowly-scoped manual-adoption workflow was then approved
    (`LocalOrderRecord.origin: "manual_adoption"`, exact ticket/symbol/side/volume match against
    a fresh live read required before adopting) before the real close succeeded.
  - **Step 6 — live-proven, both paths**: `submit()` now supports `order_type="MARKET"` via
    `_submit_market()`. `place_market_order` (confirmed by reading source) accepts only
    `symbol`/`volume`/`type` — every MARKET order opens completely naked — so a mandatory
    follow-up `modify_position` call attaches SL/TP; local state is written
    `status="OPEN_UNPROTECTED"` *before* that attempt (never after), exactly one attempt is
    made with no retry, and success requires both a confirmed-done retcode AND a fresh live
    read agreeing SL/TP now matches — retcode alone is never trusted. On failure,
    `SlTpAttachmentFailedError` is raised and the ticket stays `OPEN_UNPROTECTED`; no automatic
    retry or close is ever attempted — recovery is a separate, explicitly-approved action, never
    automated. `OPEN_UNPROTECTED` is included in `StateStore.all_open()` so reconciliation still
    treats the ticket as locally known, scoping any failure to that one ticket rather than
    forcing the whole executor into `MANAGE_ONLY`. Broker-side minimum stop-distance
    (`stops_level`/`freeze_level`) is deliberately not pre-validated locally, by explicit user
    decision — reliable `SymbolInfo` isn't available through the current MCP connection path.
    **Both live outcomes observed**: first live attempt (ticket `171617865`) hit exactly the
    designed failure path — `modify_position` rejected with retcode `10016` ("Invalid stops"),
    a live-confirmed instance of the known retcode-trust bug (the tool's own message claimed
    success), correctly left `OPEN_UNPROTECTED`, no auto-remediation attempted; recovered via a
    separately-approved `close_position()` call (retcode `10009`, verified absent). After fixing
    the SL/TP margin (a percentage-of-price floor now dominates the old
    stops_level-gap-multiplier-only formula, which was far too small for a high-priced
    instrument like BTCUSD) and a smoke-test-script-only bug (its leftover-detection check had
    been filtering live positions by `magic`, which MT5 always reports as `0` on positions this
    project places — fixed to check local `StateStore` instead), a second live attempt (ticket
    `171618036`) succeeded end-to-end: place, mandatory attach, and the script's own designed
    cleanup close all confirmed via live reads, not retcode alone. This closes out Step 6's
    live-proof requirement — every branch of `_submit_market()` is now live-proven, the same
    bar Step 4 met for LIMIT orders and Step 5 met for `close_position()`. Account is clean, no
    positions open from this work.
  - **Not yet done**: Wiring `McpOrderExecutor` into `run_grid_cycle`/`run_runner_cycle` for
    autonomous trading remains a separate, later-approved effort, out of scope for the current
    plan. Only BUY-side MARKET orders have been exercised live (SELL isn't restricted in code,
    just not yet observed). Both need their own explicit approval before any code or live call,
    per this project's established practice.
  - Full detail: `docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md`.
- **Phase 7 (regression and failure testing): in progress.** No live trading involved — this
  phase hardens the existing pipeline against failure conditions and adds regression coverage
  spanning more than one call. Found `run_grid_cycle`/`run_runner_cycle` had no failure-mode
  tests at all: any adapter exception mid-cycle propagated unhandled, and in `run_grid_cycle`
  specifically, if the SELL side's `executor.submit()` raised after the BUY side had already
  succeeded, the function raised without ever returning the BUY side's `ExecutionResult` — the
  caller lost that return value entirely (not the real state, since `McpOrderExecutor` already
  persists before returning — only the in-memory value). Fixed: new `GridCycleError` carries
  every `ExecutionResult` actually obtained (`.completed_results`) and every per-side failure
  (`.errors`) — one side's failure no longer blocks the other from being attempted, and nothing
  observed during a cycle is silently lost, while the function still always raises loudly when
  something failed. `run_runner_cycle` needed no change (single submission, no partial-results
  scenario) — confirmed by test, not assumed. Added 13 tests: failure injection at every
  read/submit stage for both cycle functions, plus a repeated-cycle regression test proving the
  duplicate-order guard works across chained invocations, not just within one call.
  State-store-at-scale sweep (+3 tests: many-tickets round trip with mixed statuses, a rapid
  sequential-mutation burst, and `reconcile()` at a 600-ticket realistic mixed scale) found a
  genuine scaling property, not just confirmed correctness: `StateStore` did a full
  load-serialize-write cycle on every single write, so N writes was O(N²) total real disk I/O.
  **Fixed**: `StateStore` now stores one JSON file per ticket (`var/order_state/<ticket>.json`)
  instead of one big file for the whole store — every write/read method except `all_open()`
  touches only its own ticket's file, O(1) regardless of total ticket count. Benchmarked flat
  ~3ms/write from 100 to 800 sequential tickets (was ~28s total for 250 tickets under the old
  format). One-time migration script (`scripts/migrate_state_store_to_per_ticket_files.py`)
  converted the real `var/order_state.json` (3 live-smoke-test records) to the new format,
  backing up the original as `var/order_state.json.migrated-bak`. `all_open()` still scans the
  whole directory (O(N)) and is called once per `McpOrderExecutor.submit()`/`cancel()`/
  `close_position()` via `_current_posture()` — this was true of the old format too and is
  **not** fixed by this change; flagged as a remaining risk only if ticket volume ever grows
  very large under sustained use. Full detail: `docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md`.
- **Phase 7: two scoped slices done, MCP disconnect/timeout now partially closed (mock/stub-only)
  — still not full failure-mode coverage, do not treat "done" as "complete."** grid_cycle failure
  handling and the state-store-at-scale sweep + the O(N²) fix are complete. The live/MCP-adjacent
  failure-testing gap flagged below is now partially closed, in two stages, both stub/mock-only —
  no MT5, no credentials, no `.env`, no live/trading call in either:
  - **Stage 1** (`tests/integration/test_mcp_client_disconnect.py`): spawns a throwaway stub stdio
    MCP server (`tests/integration/_stub_mcp_server.py`, no MT5 import) and force-kills it mid-call
    for real. Empirical finding, not assumed: `McpClient`'s real behavior on a dropped pipe is
    `mcp.shared.exceptions.McpError("Connection closed")` in well under a second — a clean
    `Exception` subtype, not the 30s `McpCallTimeoutError` path, and not an escaping
    `asyncio.CancelledError`/`BaseExceptionGroup` (a real risk with anyio-based transports,
    seriously considered and directly checked, not assumed safe). A second call on the same dead
    session, and `McpClient.__aexit__` cleanup after the kill, both also fail/complete fast, not
    hang.
  - **Stage 2** (`tests/integration/test_pipeline_loop_disconnect.py`): loads
    `scripts/run_demo_execution_pipeline_loop.py`'s `_run_one_cycle()` directly via `importlib`
    (never calls that script's `main()` — no `.env`/credentials touched) and injects that exact
    real exception type via mock market data/account + `DryRunExecutor`. Proves the existing
    blanket `except Exception` already catches it correctly, `_run_one_cycle()` returns `ok=False`
    (never raises past its own boundary), the same stop check `main()` uses prevents a later
    cycle's executor from ever being touched, and `StateStore` is left completely unchanged (no
    corrupted or partial record) — matching `McpOrderExecutor`'s own call-then-record ordering.
  - **No code fix was needed** — the existing `except Exception` handling already covered the real
    disconnect shape correctly; this closes the "is this actually true or just untested" question,
    not a bug.
  - **Timeout path also now closed, still mock/stub-only**: `test_a_real_slow_call_raises_mcp_call_timeout_error_via_a_real_pipe`
    (same file) proves `McpClient`'s real `asyncio.wait_for()` wrapping fires `McpCallTimeoutError`
    against a real subprocess/pipe (the stub cooperatively sleeping longer than the configured
    timeout, never killed) — closing the gap `tests/unit/test_mcp_client.py`'s fake-session
    version couldn't (no subprocess or pipe exists in that test at all). Confirmed the timeout,
    not the stub's own sleep completing, is what ends the call, and that cleanup of a still-alive,
    mid-sleep child doesn't hang either.
  - **Stage 3 Part 2: written, then live-verified — a real disconnect against the actual
    demo-connected subprocess, for real.** `scripts/run_demo_execution_mcp_disconnect_smoke_test.py`
    — the first script in this whole effort that touches the real demo-connected MCP server. Goes
    through the real `demo_execution_session()`, makes only a read-only `get_account_info` call
    (no `executor` reference anywhere in the file). First two live runs both aborted safely on an
    "ambiguous diff" (2 new wrapper + 2 new extended-server processes instead of 1 of each) —
    correct behavior at the time, but root-caused (not guessed) after adding full command-line
    diagnostic logging: this machine's `.venv/Scripts/python.exe` is a ~235KB CPython venv
    launcher stub (`pyvenv.cfg`: `home=miniconda3`) that spawns the base interpreter as a genuine
    *child OS process* rather than exec'ing in place, at every level of the chain (wrapper AND
    extended-server both) — one logical connection legitimately produces up to 4 real PIDs in a
    single parent→child→grandchild→great-grandchild chain, not two independent connections.
    **Fixed**: validation now requires the new-process diff to form exactly one connected process
    tree (single root, everything else a confirmed descendant, root's command line matching the
    exact Python executable used) rather than exactly 1 PID per marker — still correctly aborts if
    a genuinely unrelated second connection ever produces a second, disconnected root.
    **Live-verified end-to-end on the next run**: the full 4-process tree was correctly identified
    and tree-killed via its single root, **0 orphans** on re-scan, and a call made against the
    now-confirmed-dead connection raised `anyio.ClosedResourceError` in 0.00s — a plain `Exception`
    subtype, not `BaseExceptionGroup`/`CancelledError`, not hanging. Note: the real server's
    concrete exception class (`anyio.ClosedResourceError`) differs from Stage 1's stub finding
    (`mcp.shared.exceptions.McpError`) — both are equally safe (clean `Exception` subtypes, fast,
    never escaping as `BaseException`), but nothing in this codebase should ever assume a specific
    exception *class*; only `except Exception` is used anywhere, and that remains correct. The
    Step 3 mid-flight race (killing while a call is actively in-transit, not just after) didn't
    land on this run — the raced call happened to finish first, the documented best-effort/timing
    limitation of testing against real (fast) network calls rather than the stub's fully
    controllable sleep; the result above comes from the deterministic post-kill assertion instead,
    which still directly answers the real question. No order, no symbol, no `executor` call was
    ever made — fully read-only throughout, confirmed by the script's own design.
  - **Stage 3 Part 3: decided — accepted as an open risk, not pursued now.** The "ambiguous
    in-flight" case — a real order reaches the broker but the response is lost to the same
    disconnect — remains entirely untested, since `McpOrderExecutor` only writes local state
    *after* its MCP call returns, so this can only ever be resolved against a real broker, never a
    mock. User explicitly chose not to build/run this now rather than leave it merely unstarted:
    it's the one piece of this whole effort that needs a real order to test at all (every other
    disconnect scenario was provably closable read-only, and was), timing a kill to land on the
    exact in-flight window of a live order call isn't reliably controllable, and nothing currently
    in scope depends on it — no sustained/unattended live operation has been proposed, and
    existing reconciliation (proven correct and traced, not assumed, across ~20 real cleanup
    episodes this project has already run) already handles every *other* stale/unknown-state
    scenario correctly. Revisit if/when extended or less-supervised live operation is actually
    proposed, informed by real usage patterns at that point rather than manufactured now — same
    reasoning shape as the `all_open()` cost decision (Step 23) and the stale-`StateStore`-records
    decision (Step 24) earlier in this same effort.
  Stage 3 is now fully resolved (Parts 1–2 done and live-verified, Part 3 explicitly accepted open)
  — Phase 7's live/MCP-adjacent failure-testing gap is closed for the scope this project has
  chosen to close it at. See "Forward phases" below for Phase 8's remaining preconditions.
- **Pipeline wiring (post-Phase 7): in progress, first live cycle done and cleaned up.** Not
  one of the numbered phases — like "wire real adapters" before Phase 6, a separate,
  explicitly-approved effort, called out in both the Phase 6 and Phase 7 checkpoint docs as
  deliberately out of scope until now. This is the first time `run_grid_cycle`/`run_runner_cycle`
  are wired to the real, order-submitting `McpOrderExecutor` rather than `DryRunExecutor` or a
  hand-built `OrderPlan` (every prior real `McpOrderExecutor` call, in the three
  `scripts/run_demo_execution_*_smoke_test.py` scripts, used a hand-built plan, never the
  strategy pipeline). User chose "human-approved per cycle": one script invocation runs exactly
  one cycle, reports what happened, and requires a human to re-invoke it for the next cycle — no
  internal scheduler/loop yet. `scripts/run_demo_execution_pipeline_cycle.py` written, mirroring
  `scripts/run_live_dry_run_pipeline.py`'s exact config (symbol/timeframe/magics/strategy
  configs/exposure caps) with only the executor swapped for a real one via
  `execution/composition.py`'s `demo_execution_session()`. A `STRATEGY` constant picks exactly
  one of grid/runner per run, never both together. Key departure from every prior smoke-test
  script: this one does **not** clean up after itself — a real cycle's result is the actual
  intended strategy decision, meant to persist and be managed by later cycles, not undone by the
  same invocation.
  First live run (`STRATEGY="GRID"`, 2026-08-03): both sides submitted and verified — ticket
  `171621248` (BUY_LIMIT) and `171621249` (SELL_LIMIT), BTCUSD, magic=71101. User then approved
  cancelling both; a new one-off script,
  `scripts/run_demo_execution_cancel_pipeline_cycle_orders.py`, verified both present, cancelled
  each with exactly one attempt (no retry), and verified both absent afterward — both retcode
  10009 (done), both confirmed cancelled, account left clean (0 live pending orders).
  First live `STRATEGY="RUNNER"` run (2026-08-03) found a real, previously-invisible gap, with
  zero live impact: `run_runner_cycle()` raised `InvalidOrderPlanError` before any MCP call —
  `order_planning/plan.py`'s `build_order_plan()` defaults `sl`/`tp` to `0.0`/`0.0`, and neither
  `grid_cycle.py` nor `runner_cycle.py` ever passes real values, so every `OrderPlan` this
  pipeline layer produces is unprotected. `McpOrderExecutor` only hard-validates non-zero SL/TP
  for MARKET orders (an intentional Phase 6 safety rule) — grid's LIMIT orders went through
  fine with the same zero values, runner's MARKET order was refused outright, before reaching
  the broker. Invisible until now because every prior exercise used `DryRunExecutor`/mocks,
  neither of which validates SL/TP. **Fixed**: `strategy/runner.py`'s new
  `compute_stop_distances()` (ATR-based, reusing `features/atr.py`'s existing helper — the same
  one `strategy/grid.py` already uses — with a fixed-floor fallback) gives `run_runner_cycle()`
  a real, non-zero, correctly-ordered SL/TP to pass into `build_order_plan()`. New,
  project-original design — the legacy runner never attached SL/TP at all, so there was no
  formula to port. `run_grid_cycle()`'s parallel LIMIT-orders-unprotected question is untouched,
  left as a separate, undecided question. +4 tests (327 passed total), architecture tests still
  pass. Live-verified twice since: a dedicated, self-cleaning smoke test
  (`scripts/run_demo_execution_runner_sltp_smoke_test.py`, magic=79999, ticket `171621792`,
  SELL, retcode 10009 both submit and close, independent live re-read confirmed sl/tp matched
  exactly, account left clean), then the actual, non-cleaning pipeline-wiring script itself
  (`STRATEGY="RUNNER"`, magic=72101, ticket `171621825`, SELL, retcode 10009, verified). User
  chose to leave that position open (unlike the earlier GRID orders, which were cancelled) — to
  be picked up by a later cycle. A second live `STRATEGY="GRID"` run followed (ticket
  `171621825` still open at the time, no conflict — disjoint magics): both sides submitted and
  verified again, tickets `171621926` (BUY_LIMIT)/`171621927` (SELL_LIMIT), retcode 10009 each,
  both left open by the same no-cleanup design. A cleanup script written against that reported
  state correctly aborted before touching anything — the account had moved on since (ticket
  `171621825` already closed by its own broker-side SL/TP, ticket `171621926` filled into a real
  position). Rewritten against confirmed-current live state and re-run: all three resolved
  (`171621926` closed retcode 10009, `171621927` cancelled retcode 10009, `171621825`
  reconciled locally as `CLOSED` — no MCP call needed, already gone). Account is now clean (0
  open items from this effort).
  **Grid's parallel LIMIT-orders-unprotected gap is now fixed too**: research found
  `GridLevels.tp_price` already existed (computed by `compute_grid_levels()`) but was completely
  dropped — never read by `trade_intent/grid.py` or `pipeline/grid_cycle.py`. Grid's SL side had
  no precedent at all. Added `GridStrategyConfig.sl_atr_mult` (new, project-original, default
  `2.0`) mirroring runner's independent-multiplier pattern; `compute_grid_levels()` now computes
  `sl_price` alongside the existing, untouched `tp_price`; `grid_cycle.py` now passes both into
  `build_order_plan()`. +4 tests (330 passed total), architecture tests still pass.
  **Live-verified once, found a real bug**: SELL submitted and verified correctly (ticket
  `171622543`, retcode 10009, left open on the account), but BUY was rejected —
  `sl`/`tp` were computed relative to `intent.reference_price` (the pre-normalization
  `center ± step_price` level), not the final, broker-normalized `plan.price`; when
  `normalize_limit_price()` pushed this BUY's entry down by over 160 points, the anchored SL
  ended up above the actual price, rejected client-side (`"Stop loss must be less than price"`,
  no live impact). **Fixed**: `grid_cycle.py` now anchors `sl`/`tp` to the actual `plan.price`
  (post-normalization) via `dataclasses.replace()`, not `intent.reference_price`. New regression
  test deliberately forces `normalize_limit_price()` to push the entry far, and was verified (via
  `git stash` on just the fix) to actually fail pre-fix and pass post-fix — not just assumed to.
  +1 test (331 passed total), architecture tests still pass. **Live re-verified**: a second live
  GRID run submitted both sides successfully with correct SL/TP ordering (BUY ticket
  `171622789`: `sl<price<tp`; SELL ticket `171622791`: `tp<price<sl`; both retcode 10009,
  verified). Account holds 3 open grid items (those two plus `171622543` from the first run),
  all correctly protected — user chose to leave all three open, for a later cycle to manage.
  **Bounded autonomous loop designed and implemented, not yet run live**: the option
  deliberately deferred when this effort started. Four structural decisions made explicitly
  first — both strategies every cycle (isolated per-cycle), stop-file + `Ctrl+C`, stop
  immediately on any cycle error (no retry/tolerance in v1), one long-lived connection for the
  whole run (a drop is fatal, no reconnect logic). New `pipeline/loop_control.py`
  (`should_stop()`/`LoopLimits`, the one piece of genuinely new decision logic, kept pure and
  unit-tested) and `scripts/run_demo_execution_pipeline_loop.py` (thin orchestration shell,
  conservative first-run defaults: 5-min cycle interval, 12-cycle/90-minute hard ceilings, adds
  a per-run file log under `var/logs/`). +10 tests (341 passed total), architecture tests still
  pass.
  **First live run: apparent hang was a false alarm (stdout buffering, not a real stuck call —
  confirmed by exact timestamp evidence), but investigating it surfaced a real credential
  exposure** (the demo account password was visible in `metatrader_mcp_extended_server.py`'s
  process command line via ordinary process listing). Stopped the loop safely via the stop-file
  (clean exit, ~5s). All 6 tickets the loop's 2 completed cycles created were resolved — 4 had
  already closed live via their own SL/TP before cleanup even started (real proof the SL/TP
  fixes provide genuine, triggering protection), the remaining 2 cancelled/closed explicitly,
  each re-verified live immediately before acting. **Three fixes made** (code only, not yet
  live-verified): credentials no longer passed via argv (env-var fallback added to the local
  extended-server script instead); `McpClient.call_tool()` now has a 30s timeout
  (`McpCallTimeoutError`), the first bound on any MCP call anywhere in this codebase; the loop
  script's own reporting now goes through `logging` instead of `print()`, fixing both the
  buffering lag and the file log missing its own output. +5 tests (346 passed total),
  architecture tests still pass. **End-of-day safe-stop confirmed**: no processes running, live
  state re-checked (only the 2 pre-existing tickets from before the loop run remain, all 6 of
  the loop's own tickets confirmed cleaned up, nothing unexpected), tests still 346/13 passing,
  nothing left uncommitted. **Live testing paused — will not resume without explicit approval.**
  **Magic-filter bug fixed (code only, not yet live-verified)**: `run_grid_cycle()`/
  `run_runner_cycle()` (`pipeline/grid_cycle.py`, `pipeline/runner_cycle.py`) now accept an
  optional `state_store` parameter. When supplied, `open_lots`/`pending_lots`/the duplicate-
  order check are computed from an unfiltered live read cross-referenced against
  `StateStore.all_open()`'s `LocalOrderRecord.magic` (the intended magic recorded locally at
  submission time, never the broker's echoed-back `0`) instead of trusting
  `account.get_positions()`/`get_orders(symbol, magic=magic)`'s broken client-side filter.
  Omitting `state_store` (the default) preserves the exact prior behavior, so every mock/dry-run
  caller is unaffected. Wired into all three real-executor call sites
  (`scripts/run_demo_execution_pipeline_cycle.py`, `scripts/run_demo_execution_pipeline_loop.py`,
  `scripts/run_demo_execution_runner_sltp_smoke_test.py`); `scripts/run_live_dry_run_pipeline.py`
  needed no change (`DryRunExecutor` has no `StateStore`). +2 regression tests reproducing the
  magic=0 quirk in a mock for the first time — each asserts both the still-blind fallback
  without `state_store` and the corrected behavior with it — 348 passed total, architecture
  tests still pass. **Live-verified**: one real GRID cycle (both sides submitted and protected,
  tickets `171647522`/`171647525`) and one real RUNNER cycle (MARKET order placed but its
  mandatory SL/TP attach was rejected — retcode `10016`, the same live-confirmed retcode-trust
  bug from Phase 6 Step 6, recurring on ticket `171647565` — correctly left `OPEN_UNPROTECTED`,
  no auto-remediation, then closed via a separately-approved recovery call, retcode `10009`,
  verified absent) produced real, populated, differently-magic-tagged live data. Re-running the
  fix against it confirmed correct discrimination: grid's magic (`71101`) recovered its own 2
  live pending orders (`pending_lots=0.02`) via `state_store`, while runner's magic (`72101`)
  correctly stayed at zero rather than misattributing grid's tickets — exactly the gap this fix
  was written to close, now proven against the real broker, not just mocks. **Both grid tickets
  now resolved**: `171647522` was already gone from live state on re-check (filled then closed
  via its own SL/TP) and was reconciled locally as `CLOSED`; `171647525` was cancelled (retcode
  `10009`, verified absent). Account is clean — 0 live positions/orders on BTCUSD.
  **Confirmed `ExposureCaps`' own intent (read-only, no code change)**: `risk/portfolio_guards.py`
  was always designed to count pending LIMIT orders as real exposure, not just open positions —
  its docstring and `check_exposure_cap()`'s own `projected_total = open_lots + pending_lots +
  proposed_volume` computation confirm it directly. Step 17's 12 straight grid cycles blowing past
  a 0.06-lot cap was never a design gap in this guard; it was purely the magic-filter bug feeding
  it `open_lots=0.0`/`pending_lots=0.0` regardless of real state — now fixed and live-verified, so
  the cap should genuinely protect pending-order exposure going forward.
  **Quantified `StateStore.all_open()`'s per-call cost with a real benchmark (read-only, no code
  change)**: ~24 ms/call against the real 57-ticket directory today, but ~1.3 ms/ticket-file
  scaling confirmed synthetically (1.4s at 1000 tickets, 2.7s at 2000, 6.2s at 5000) — negligible
  now, but a genuine, evidence-backed problem for sustained live use, since `run_grid_cycle()`/
  `run_runner_cycle()` (post Steps 18-19) plus `McpOrderExecutor` actions can call it 4-5 times
  per real cycle, and nothing ever prunes closed tickets from the directory. Not fixed — flagged
  as the clear next candidate if this project moves toward sustained (not just bounded-test) live
  operation.
  **Decided: not fixing it now.** Doesn't block anything currently in scope (no sustained live
  operation has been proposed), and the candidate fixes (in-session caching, a per-magic index,
  archiving) all carry real risk — caching in particular could feed stale reads to the exposure-
  cap/duplicate-order guards, the highest-severity failure class for this codebase. Revisit if/
  when sustained live operation is actually proposed, informed by real usage patterns at that
  point rather than guessed now.
  **Stale `StateStore` records (tickets closed live via their own SL/TP, never reconciled by an
  explicit call) traced, not assumed, harmless**: neither `determine_posture()` nor the
  `cancel()`/`close_position()` `MANAGE_ONLY` gate nor the magic-recovery fix can be misled by a
  stale record — confirmed by reading each consumer directly. Quantified: 39 of 57 local ticket
  files are currently marked `OPEN` even though the account is fully flat — 100% stale right now,
  and still harmless. Not fixed — the only real fix options either violate this project's
  explicit "never mutate local state automatically" principle or duplicate the `all_open()` cost
  decision already deferred above.
  **Third live loop run: exposure cap confirmed binding for the first time, safe midday stop.**
  Launched a fresh bounded autonomous loop (same conservative config as Step 15/17) to confirm the
  exposure cap and duplicate-order guard now actually bind in a real multi-cycle run. Ran 6 of 12
  cycles: 14 tickets submitted (8 grid, 6 runner), all retcode 10009/verified, every runner SL/TP
  attach succeeded first try. **Cycles 5 and 6's grid submissions were both correctly rejected by
  the exposure cap** (`projected_total=0.06 exceeds max_open_lots=0.06`) — the exact scenario
  Step 17 exposed as silently broken, now genuinely enforced live, closing the loop on this whole
  investigation's original goal. Stopped safely mid-run via the project's normal stop-file
  mechanism (ahead of a lunch break, user-requested): no cycle 7 started, zero errors across the
  run. 8 of the 14 tickets already resolved on their own (closed via broker-side SL/TP) before the
  post-stop check ran; 6 remain live (2 positions, 4 pending grid orders), all protected with real
  SL/TP, no cleanup performed (matches the loop's own no-cleanup design) — safe to leave over a
  break. `var/STOP_PIPELINE_LOOP` is still present on disk and must be deleted before any future
  loop relaunch, or it will exit immediately (same gotcha Step 17 hit).
  **Step 26 (new session)**: checked live state of Step 25's 6 leftover tickets — all 6 already
  absent (closed via their own broker-side SL/TP), account fully flat. Reconciled all 6 stale
  local `StateStore` records to `CLOSED` via `record_closed()` only, no MCP call needed since
  nothing remained live on the broker side. No production code changed.
  **Step 27**: fourth live loop run, user-approved relaunch with unchanged config. Grid submitted
  and protected both sides (tickets `171651878`/`171651879`); runner's SL/TP attach hit the
  retcode-trust bug for the third time (`171651880`, retcode `10016`, tool message falsely claimed
  success) — loop correctly stopped itself after cycle 1 (no error tolerance), position correctly
  left `OPEN_UNPROTECTED`, no auto-remediation. User approved closing `171651880` (retcode `10009`,
  verified absent); user chose to leave the 2 grid tickets open. Follow-up same session: a later
  check found `171651878` already closed on its own (broker-side SL/TP); reconciled locally
  (`171651879` still genuinely live, left untouched). No production code changed.
  Second follow-up: `171651879` was later found closed on its own too (broker-side SL/TP) and
  reconciled locally the same way — every ticket from Step 27's loop run is now resolved, account
  fully clean (0 live positions/orders).
  **Step 28**: root-caused the recurring retcode-10016 pattern (read-only, no code change). Traced
  to the vendored `metatrader_client` package's `send_order()` SLTP branch
  (`.venv/Lib/site-packages/metatrader_client/order/send_order.py:272-277`), which determines
  `success` from `mt5.last_error()` (a terminal/API-level code) rather than the broker's real
  `response.retcode` — the exact source of "Known Issues item 7" for `modify_position`
  specifically. The message's "current price 0.0" is a red herring: `response['data'].price` is
  structurally `0.0` on every SLTP response (success or fail), since MT5 never populates an
  execution price for a pure stop-modification. Confirmed our own `mcp_order_executor.py` already
  handles this correctly (never trusts the tool's own success field; parses retcode directly;
  requires a fresh live re-read before confirming) — all 3 recorded occurrences were caught
  correctly. Retcode `10016` itself is a separate, already-anticipated broker-side
  stops_level/freeze_level rejection, not explained or caused by the trust bug. **Watch item
  closed, no fix needed.**
  **Step 29**: fifth live loop run, user-approved relaunch with unchanged config. Ran 4/12 cycles
  (6 grid tickets + 3 runner positions all succeeded in cycles 1-3) before cycle 4's runner MARKET
  order (`171653006`) hit the same retcode-10016 bug a fourth time — loop correctly stopped
  itself, position correctly left `OPEN_UNPROTECTED`. User approved closing `171653006` (retcode
  `10009`, verified absent). Follow-up checks found 6 more tickets from this run had since
  self-resolved via their own broker-side SL/TP (`171652731`, `171652799`, `171652801`,
  `171652846`, `171653005`, `171652845`); all reconciled to `CLOSED` locally, no MCP calls. 5
  tickets remain live and protected (1 position, 4 pending grid orders), left open by design.
  Follow-up, later session: a full read-only reconciliation found all 5 had also since
  self-resolved via their own broker-side SL/TP; reconciled to `CLOSED` locally, no MCP calls.
  Every ticket from Step 29's loop run is now resolved; account fully clean. No production code
  changed.
  **Step 30**: sixth live loop run, user-approved end-to-end unattended run (stop only on the
  loop's own designed conditions, report once at the end). Ran 3/12 cycles — 4 grid tickets + 2
  runner positions succeeded in cycles 1-2, cycle 3's grid succeeded too, but its runner MARKET
  order (`171654324`) hit the retcode-10016 bug a fifth time — loop correctly stopped itself,
  position correctly left `OPEN_UNPROTECTED`. Per the approved test design, **not** auto-closed —
  recovery for an unprotected position remains a separate, explicitly-approved action; it is a
  real, unprotected live position awaiting a decision. 3 of the 8 protected tickets were found
  self-resolved and reconciled locally (no MCP calls); 5 protected tickets remain live untouched.
  User then approved closing `171654324` (retcode `10009`, verified absent) — 0 unprotected
  positions remain. No production code changed.
  **Step 31 (end-of-day safe stop)**: user manually cancelled the 5 remaining pending grid orders
  directly in MT5; reconciled locally via `record_cancelled()` (no MCP calls). Final
  independently-re-verified state: 0 live positions, 0 live pending orders, no process running, no
  stop-file, all local records for tickets touched this session reconciled to a terminal status.
  Retcode-10016 recurred 3 more times today (Steps 27/29/30, 5 occurrences total project-to-date)
  — every single time caught and handled correctly by the Step 28 mitigation, zero unmanaged risk
  left behind. No production code changed. **Live testing remains paused.**
  Full detail: `docs/PIPELINE_WIRING_CHECKPOINT.md`.

Full session-by-session detail for the "wire real adapters" step (now fully complete) is in
`docs/MCP_ADAPTER_WIRING_CHECKPOINT.md`. Phase 6 itself is tracked separately in
`docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md`, Phase 7 in
`docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md`, pipeline wiring in
`docs/PIPELINE_WIRING_CHECKPOINT.md`, Phase 8 in
`docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md`, the grid regime filter (a new, separately-scoped
effort motivated by Phase 8's Step 7 finding, **CLOSED as a negative result**) in
`docs/GRID_REGIME_FILTER_CHECKPOINT.md`, Phase 9 (**Steps 1-6 done, Step 7 scoped/sized
(MAX_DAILY_LOSS=50.0, MAX_CYCLES=30), two more live attempts made 2026-08-07 after the lunch break:
run #2 (backgrounded-Bash launch) was killed abruptly at cycle 11/~50min -- root-caused as a
duration cap on Bash-tool-backgrounded tasks in this tool session (not a project bug, no orphan
process, account safe); run #3 (relaunched as a fully detached OS process) confirmed the fix,
running cleanly to 16/30 cycles before a clean end-of-day stop via the stop-file. Still no
unbroken 30-cycle window completed, kill-switch still unobserved at Step 7 scale. Account left at
1 open protected position + 5 pending grid orders, 0.06 lots, zero OPEN_UNPROTECTED. Future Step 7
attempts should launch via the detached-process pattern, not backgrounded Bash.**) in
`docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`
(see also `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md`), and operational reliability hardening (a
new, separately-scoped effort named by Phase 9's own Design section and the readiness checklist's
rows 8-9, **scoped and decided 2026-08-07: both items left as-is, no code changes needed for
now**) in `docs/OPERATIONAL_RELIABILITY_HARDENING_CHECKPOINT.md` — read whichever is relevant
before continuing that work in a new session.

## Forward phases (named, not yet scoped)

Referenced informally across pipeline-wiring checkpoint entries (`docs/PIPELINE_WIRING_CHECKPOINT.md`,
"Remaining roadmap") but never formally defined here until now. Unlike phases 0–7 above (phases of
*building* this codebase), these are phases of *running and tuning* the strategy once built — a
different kind of work, each still requiring its own explicit scoping and approval before any code
is written, per this project's normal workflow. Phase 9 is now scoped, with Steps 1-6 done — the
kill-switch is built, wired, and live-verified, and a demo-to-live readiness checklist exists
(`docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md`, mostly NOT MET today — honest, expected, since Step 7
hasn't run yet) — see `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`. Live pilot still has no checkpoint
doc, and writing
detailed entry/exit criteria for it is itself a future, explicitly-approved task — not done here,
to avoid designing ahead of what's actually been asked for.

- **Phase 8 (strategy research, edge validation, parameter tuning, regime analysis,
  transaction-cost/stress testing, walk-forward/out-of-sample validation)**: **scoped, not
  started** — full detail in `docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md`. No tuning framework,
  walk-forward harness, regime classifier, or transaction-cost model exists anywhere in this
  codebase yet; no step has been built. Phase 7's live/MCP-adjacent failure-testing gate is
  cleared (Stage 3 Parts 1–2 done and live-verified, Part 3 explicitly accepted as an open risk),
  so Phase 8 is no longer blocked on that. **Decided while scoping**: `strategy/guard.py`'s
  EMA exit-guard (`evaluate_guard()` — real, ported, unit-tested, but never wired into `pipeline/`
  or any live script, confirmed by grep) is explicitly out of scope for this phase; Phase 8 tunes
  and validates grid/runner exactly as they run today, with no exit-guard. Wiring the guard in is
  a separate, not-yet-scoped effort, closer in kind to "pipeline wiring" than to tuning
  already-deployed behavior. Requires its own explicit scoping/approval before any code is
  written, same as every phase in this project. **Step 1 done**: edge metric decided —
  per-trade expectancy in R-multiples (P&L ÷ risk-per-trade), net of transaction costs, computed
  separately per strategy (grid/runner never blended), paired with max drawdown in the same R
  units as a required companion metric, no "edge validated" claim below a 30-trade-per-strategy
  minimum sample. Full reasoning in the checkpoint doc. **Step 2's discovery action done and
  live-verified**: a read-only historical-data probe (`scripts/run_demo_execution_historical_data_probe.py`)
  confirmed real `BTCUSD` history on this demo terminal goes back to 2015-01-01 (via uncapped
  `H4`/`D1` results); `M1`–`H1` all returned exactly the requested 50,000-bar ceiling, meaning
  more exists than measured — not yet a problem, since even the confirmed depth (35+ days of
  `M1`, 6+ years of `H1`) comfortably supports Step 1's 30-trade-per-strategy minimum with room
  to spare. **Step 2 now fully done**: new `backtest/` package (`market_data_cache.py` —
  `cache_path()`/`save_bars()`/`load_bars()`/`merge_bars()`, pure file I/O, no adapter imports,
  10 unit tests) stores one CSV per symbol+timeframe under `var/market_data/` (git-ignored, stdlib
  `csv`, no new dependency — `pyproject.toml` has zero hard runtime deps). Live-seeded via
  `scripts/run_demo_execution_historical_data_cache_seed.py` (read-only, no `executor` reference):
  `var/market_data/BTCUSD_M1.csv` now holds 50,000 real `M1` bars
  (`2026-07-01T01:59` → `2026-08-05T06:48`), confirmed on disk. `M1` chosen as the only cached
  timeframe for now, matching what grid/runner actually trade live. **Step 3 now fully done**:
  new `backtest/engine.py`/`ledger.py`/`metrics.py` drive `run_grid_cycle()`/`run_runner_cycle()`
  completely unmodified against replayed bars (same seam `DryRunExecutor`/mocks already exploit),
  `ReplayCursor.visible_bars()` as the one look-ahead-bias control point, spread-only cost
  modeling (`get_deals` exposes commission/swap only for real historical deals, not usable
  per-bar). 31 new tests, including one that caught a real bug (a position filling mid-call was
  also being exit-checked in that same call — fixed via `just_filled` tracking) before it ever
  reached real data. Run against the real cached `BTCUSD` `M1` history and found a second real
  bug along the way: the engine was evaluating a new cycle every single bar, 5x more often than
  `scripts/run_demo_execution_pipeline_loop.py`'s real `CYCLE_INTERVAL_SECONDS=300` cadence —
  fixed via a new `cycle_interval_bars` parameter. Corrected run's real result: **both strategies
  show negative expectancy** at current default parameters over the cached ~35-day window (grid:
  43 trades, −0.308 R; runner: 9,881 trades, −0.159 R, 1,662 R max drawdown) — runner's volume and
  drawdown trace to a real, previously-invisible strategy gap `run_runner_cycle()`'s own docstring
  already admits: no re-entry throttle beyond the raw exposure cap. **First read, not a verdict**
  — Step 4/5 hadn't run yet, no production strategy code touched at this point.
  **Runner's re-entry throttle: fixed, this phase's first production code change.** User decided
  to fix it now rather than tune on top of it. New `risk/symbol_guards.py` guard,
  `check_position_limit()` (same pattern as `check_duplicate_order`, combined via `combine([...])`
  alongside the exposure cap — no guard skippable by another passing), gated by a new
  `RunnerStrategyConfig.max_concurrent_positions` field (default `1`: at most one open runner
  position per magic at a time, the simplest option considered). +7 tests. **Live-proven, not just
  assumed correct**: re-ran the same real backtest (which reuses `run_runner_cycle()` unmodified,
  so the fix flowed through automatically) — runner's trade count roughly halved (9,881 → 4,961)
  and max drawdown fell 43% (1,662 R → 950 R); expectancy is essentially unchanged (−0.159 R →
  −0.182 R, within noise) — **exactly the expected, honest outcome**: a re-entry throttle bounds
  how much a losing edge compounds, it doesn't fix the edge itself. Grid's numbers are bit-for-bit
  identical before/after, confirming the fix touches nothing outside `runner_cycle.py`.
  **Step 4 now fully done**: one shared `half_spread_price()` helper (replacing two duplicated
  formulas) scaled by a new `spread_multiplier` param on `run_backtest()`, run live at 1x/2x/5x
  against the real cached data via one real `get_symbol_info` call reused across all three
  levels. Real, interpretable finding: **grid is only mildly cost-sensitive** (expectancy stays
  −0.31 to −0.37 R across all three levels — its negative edge isn't primarily a cost problem);
  **runner is severely, monotonically cost-sensitive** (expectancy −0.182 R → −0.852 R, win rate
  27.3% → 4.9%, from 1x to 5x spread), implicating its `sl_atr_mult=1.5` default as too tight
  relative to realistic execution costs — real, actionable prioritization evidence for Step 5, not
  a blind sweep. **Step 5: IN PROGRESS, stopped deliberately for a midday break before winner
  selection — no production default changed.** Cache expanded live (95,000 `M1` bars now, ~67
  days — the original 50,000 was the probe script's request ceiling, not the real depth).
  `backtest/market_data_cache.split_bars()` (new, +6 tests) splits by time into a 76,000-bar
  train window and a 19,000-bar held-out test window, never touched this step.
  `scripts/run_demo_execution_backtest_tuning_sweep.py` (new) ran a one-factor-at-a-time sweep
  against the training window only — runner's `sl_atr_mult` (candidates 1.5–4.0) and grid's
  `step_mult` (candidates 0.3–0.8), full results table in the checkpoint doc. **Step 5 now fully
  done**: tables interpreted (runner's 3.0 is a genuine interior peak; grid's original "best at
  0.8" note was a misread — 0.3 already beat it — so the range was widened to 0.15–0.8, which
  found grid's real interior peak at 0.25, not a continuing low-edge trend), and a candidate
  parameter set decided for Step 6: runner `sl_atr_mult=3.0`/`tp_atr_mult=6.0`, grid
  `step_mult=0.25`. **No production default changed** — both remain training-window candidates
  only. **Step 6 (walk-forward/out-of-sample validation) now done**: ran both the current
  defaults and the Step 5 candidates against the held-out test window for the first time.
  Runner's `sl_atr_mult=3.0`/`tp_atr_mult=6.0` **validated out-of-sample** — the training-window
  improvement held (actually grew) on unseen data, both in expectancy (−0.241 R → −0.100 R) and
  drawdown (463 R → 63 R). Grid's `step_mult=0.25` **did not validate** — its apparent
  training-window edge (0.077 R) collapsed to noise (0.015 R) out-of-sample, a textbook
  overfitting signature; the current grid default stands. Neither strategy shows a positive edge
  on this window. **Runner's validated candidate has now been adopted as the production
  default**: `RunnerStrategyConfig.sl_atr_mult=1.5→3.0`, `tp_atr_mult=3.0→6.0` (2:1 ratio
  unchanged) in `src/mt5_mcp_trading/strategy/runner.py`, the third production-code change of
  Phase 8 (after the re-entry throttle and the earlier magic-filter fix already covered above).
  Grid's default is untouched. **Live-verified**: a single, explicitly-approved, self-cleaning
  smoke-test run (`scripts/run_demo_execution_runner_sltp_smoke_test.py`, magic=79999) opened a
  real SELL position (ticket `171702598`, retcode 10009) with the new SL/TP correctly ordered and
  at the new 2:1 ratio, independently re-verified live, then closed (retcode 10009, verified
  absent) — account left clean. This closes Phase 8's last open item; the phase is functionally
  complete (edge validation, cost sensitivity, tuning, walk-forward validation, production
  adoption, and live verification all done). Live/order-submitting testing returns to paused
  status — that smoke test was a single, narrowly-scoped exception, not a general resumption.
  **Grid's negative expectancy remains open**: a follow-up `sl_atr_mult` sweep (training window)
  looked like the cleanest signal of the whole phase — strictly monotonic across every tested
  value — but was found, by direct check against the numbers, to be an R-unit measurement
  artifact (grid's `tp_price` is independent of `sl_atr_mult`, so widening the stop only inflates
  the R denominator; expectancy can only approach 0 from below this way, never genuinely turn
  positive, and the shrinking R-drawdown number masks real dollar risk per trade actually
  *growing*). Not adopted. **Follow-up coupled sl/tp sweep at a fixed 2:1 reward:risk ratio
  (matching runner's own convention) run to test SL/TP shape without that artifact** — a real,
  clean result this time (confirmed via `expectancy = 3×win_rate−1` matching every row exactly):
  every single candidate came back worse than the current default (best −0.378 R vs. default
  −0.302 R), win rate collapsing to 14–21%, well below the ~33% a 2:1 ratio needs to break even.
  Four hypotheses now checked and rejected for grid (cost, step-spacing, decoupled SL, coupled
  fixed-ratio) — the strongest evidence yet that grid's negative expectancy is an entry-timing
  quality problem, not an SL/TP-shape problem; every SL/TP lever this architecture exposes has
  now been tried. **Step 7 (regime analysis) run and found a real, clean, genuinely informative
  result**: new `features/regime.py` (`efficiency_ratio()`, Kaufman's Efficiency Ratio, same
  pattern/conventions as `features/atr.py`) classified each of grid's 119 training-window trades
  by market condition at entry, split at the median. Ranging trades: 58, win rate 72.4%,
  expectancy −0.102 R, drawdown 7.920 R. Trending trades: 61, win rate 41.0%, expectancy
  −0.492 R, drawdown 30.720 R. Grid's negative expectancy is not uniform — it's disproportionately
  driven by trending conditions (still negative in ranging conditions, but far less damaging).
  Not a positive edge in either regime, but a real diagnosis that makes a future regime *filter*
  a concrete, evidence-backed idea, not adopted or built here. **Phase 8's originally-scoped
  work, through Step 7, is now complete.** Full detail in
  `docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md`'s "Exact next smallest task."
- **Grid regime filter (post-Phase-8): CLOSED, negative result.** Not one of the numbered
  phases — like "wire real adapters" before Phase 6 or "pipeline wiring" after Phase 7, a
  separate effort motivated by Phase 8's Step 7 regime-analysis finding. User explicitly closed
  this effort out after both candidates it produced failed out-of-sample validation (see below) —
  no viable Efficiency-Ratio threshold was found for a grid regime filter on this symbol/window.
  `GridStrategyConfig.max_entry_efficiency_ratio`/`efficiency_ratio_period` (Step 2) remain in the
  codebase, opt-in and default-`None`/`14`, tested but unused — dead but harmless. Grid's default
  configuration is unchanged from Phase 8. Full scoping/design/history in
  `docs/GRID_REGIME_FILTER_CHECKPOINT.md`; re-opening needs a genuinely new idea, not a repeat
  threshold search, per that doc's closing section.
  **Step 1**: a training-window threshold sweep (via two new opt-in parameters on
  `backtest/engine.py`'s `run_backtest()`, simulating the filter's dynamic exposure-cap effect
  without building the production change first) found and confirmed a genuine interior peak:
  `max_entry_efficiency_ratio=0.2` (expectancy -0.302 R -> -0.209 R, drawdown 37.120 R ->
  29.920 R, trade count 119 -> 141). **Step 2**: built the actual opt-in filter --
  `GridStrategyConfig.max_entry_efficiency_ratio`/`efficiency_ratio_period` (default off), gated
  in `pipeline/grid_cycle.py` right after the bars fetch, mirroring `runner_cycle.py`'s
  FLAT-signal skip ordering exactly. +4 integration tests. **Step 3**: validated `0.2` against
  the held-out test window (real pipeline path, not the simulation hook) -- **did NOT validate**.
  Expectancy reversed from an improvement to a degradation, the same overfitting signature grid's
  `step_mult=0.25` candidate showed in Phase 8 Step 6. Runner's numbers were byte-for-bit
  identical in both test runs, confirming the filter stayed correctly isolated to grid.
  **Second Step 1 attempt (same day)**: a wider sweep (0.01-1.0) found an initially-suspicious
  isolated-looking spike at `0.01`; a fine-grained probe (0.005-0.02) around it revealed a
  genuine ~10-point plateau (`0.005`-`0.015` all substantially beat baseline), not a fluke --
  best point `max_entry_efficiency_ratio=0.013` (expectancy -0.098 R vs. -0.302 R baseline, win
  rate 72.8%, drawdown 22.960 R, 235 trades). **Step 3 for `0.013` now run against the held-out
  test window (fully offline, zero MCP/MT5 calls) -- REJECTED.** Expectancy improvement held
  out-of-sample (-0.311 R baseline -> -0.219 R, unlike `0.2` which reversed outright), but max
  drawdown -- the required companion risk metric -- nearly tripled versus baseline (14.240 R ->
  43.718 R), the opposite of the training-window finding (37.120 R -> 22.960 R), driven by the
  filter freeing up far more exposure-cap headroom on this window (45 -> 197 trades) than on the
  training window. Fails this project's own acceptance bar, which has consistently required both
  expectancy and drawdown to hold together. No production default changed anywhere in this effort
  -- `max_entry_efficiency_ratio` still defaults to `None` everywhere. **User then explicitly
  closed this effort out as a negative result** -- both candidates produced by this effort are
  rejected, and re-opening it needs a genuinely new idea (different signal, different
  windowing/split strategy), not a repeat threshold search over the same signal/window
  combination. Full detail in `docs/GRID_REGIME_FILTER_CHECKPOINT.md`'s "Effort closed" section.
- **Phase 9 (locked-parameter demo forward test, performance monitoring, drawdown/risk gates,
  operational reliability, demo-to-live readiness criteria)**: **scoped, Steps 1-3 done** — full
  detail in `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`. No automated performance/drawdown monitor
  or demo-to-live readiness gate exists anywhere in this codebase yet. Explicitly framed against
  Phase 8's honest backdrop — both strategies still show negative held-out expectancy, so this
  phase proves operational machinery and measures backtest-vs-forward drift, it does not assume
  profitability. **Step 1 (locked parameter set) done**: today's production defaults documented
  and verified directly against source (`strategy/grid.py`, `strategy/runner.py`,
  `sizing/money.py`, `risk/portfolio_guards.py`, `scripts/run_demo_execution_pipeline_loop.py`) as
  the single source of truth for the rest of this effort. **Step 2 (loss-based kill-switch guard)
  done**: new `risk/daily_loss_guard.py` -- same independent/composable `RiskDecision` shape as
  `portfolio_guards.py`/`symbol_guards.py`, closing the hard blocker this doc's Live pilot entry
  has flagged since before Phase 9 existed. `check_daily_loss_limit()` trips (at-or-beyond, not
  strictly-beyond -- a deliberate departure from `check_exposure_cap()`'s "exactly at cap is not a
  violation" convention, since this is a safety-critical stop-loss-shaped gate) once realized $
  P&L since `daily_reset_boundary()`'s reset point is a genuine loss meeting the configured
  threshold. A real bug was caught by the tests before shipping: the first version's
  `pnl <= -limit` comparison misfired on an exact `$0.00` breakeven at `max_daily_loss=0.0`
  (`0.0 <= -0.0` is `True` in floating point) -- fixed by requiring a genuine loss (`< 0`) before
  comparing magnitude. +18 unit tests, all synthetic P&L/datetimes, no adapter or live call
  anywhere -- confirmed by repo-wide grep NOT wired into `pipeline/grid_cycle.py`,
  `runner_cycle.py`, `loop_control.py`, or any script yet. **Step 3 (wire it into the loop) done**:
  `pipeline/loop_control.py`'s `should_stop()` gained an optional `daily_loss_decision` parameter
  (default `None`, so every pre-Step-3 caller is unaffected); precedence stop-file > daily-loss
  breach > max_cycles > max_runtime. Deliberately did NOT touch
  `scripts/run_demo_execution_pipeline_loop.py`'s own two real `should_stop()` call sites --
  the real script has no `realized_pnl_since_reset` source yet (Step 4's job), so wiring the live
  call sites now would mean either a fake value or building the real computation out of order;
  the live script's behavior is completely unchanged by this step. +8 unit tests (including one
  feeding the real `check_daily_loss_limit()` output straight in, not a hand-built decision), +4
  integration tests proving a real multi-cycle run (driven against the live script's own
  `_run_one_cycle()` with `DryRunExecutor`, reusing `test_pipeline_loop_disconnect.py`'s harness)
  actually halts before touching a further cycle's executor on an injected breach. A real fixture
  bug was caught along the way: the first draft used 16-bar fixtures, which made
  `run_runner_cycle()` raise on every cycle regardless of any loss logic, so every test "passed"
  for the wrong reason -- caught by checking actual cycle counts, fixed by switching to the
  40-bar `_runner_bars()` fixture already used elsewhere for the same reason. 458 passed total,
  architecture tests still pass, no live/demo call in any of Steps 1-3.
  **Step 4 (live performance/drawdown monitor) DONE, live-verified, both follow-ups resolved**:
  `StateStore.all_closed()`, a new `Deal` domain model (two wire-format corrections found by
  tracing past the docs into vendored source -- `type` is a raw MT5 enum int not a string,
  `time` arrives naive and needs explicit UTC attachment), `mt5_adapter/mcp_deal_history.py`
  (`McpDealHistoryReader`, reuses `parse_dataframe_csv()`), and `monitoring/live_performance.py`
  (`build_closed_trades()` joins by `position_id` only, never `deal.magic`;
  `realized_pnl_since()` sums real $ P&L for a trusted ticket set) are all built and unit tested.
  `scripts/run_demo_execution_live_performance_monitor.py` (read-only, `get_deals` only, no
  `executor` reference) was written, then live-verified for real (2026-08-06, explicit
  go-ahead): grid 19 trades/-0.825 R expectancy/16.302 R drawdown, runner 13 trades/-0.418 R
  expectancy/10.214 R drawdown -- both negative, consistent with Phase 8, neither past the
  30-trade minimum sample yet. Two things the live run surfaced were both root-caused: 2 skipped
  tickets (`171648990`/`171649461`) were confirmed cancelled grid LIMIT orders that never filled
  (one real `get_orders` call, `state='2'`/CANCELED, `volume_current == volume_initial`) --
  correctly excluded, not a bug; 5 unrecognized-magic trades were identified from local records
  alone (no live call needed) as known `magic=79999` Phase 6/8 smoke tests, also correctly
  excluded. One structural, unfixed finding surfaced along the way: this project's local
  `"CLOSED"` status is written identically by several reconciliation scripts for both "filled
  then closed" and "cancelled/expired unfilled" -- flagged, not retroactively relabeled. 488
  passed total after Step 4, architecture tests pass, no live/demo call in building any of it
  (only the explicitly-approved monitor run and the `get_orders` diagnostic).
  **Step 5 (live kill-switch smoke test) SCOPED AND PARTIALLY BUILT -- wiring + tests only, NOT
  RUN LIVE.** Per the checkpoint doc's own Proposed Steps table, Step 5 is specifically "a single
  short live smoke test proving the wired kill-switch actually halts a REAL loop run" -- the one
  live/order-adjacent step in the whole plan. `monitoring/live_performance.py` gained
  `compute_daily_loss_decision()` (combines `realized_pnl_since()` with
  `risk.daily_loss_guard.check_daily_loss_limit()`). `scripts/run_demo_execution_pipeline_loop.py`
  is now wired at both real `should_stop()` call sites via a new `_daily_loss_decision_for_cycle()`
  (never raises -- FAILS CLOSED on a computation error, e.g. a failed `get_deals` call, rather
  than silently skipping the check), computed once per loop iteration (one real `get_deals` call
  per cycle, not per guard check, short-circuited to zero calls when the threshold is unset).
  `MAX_DAILY_LOSS` defaults to `None` (kill-switch present but inert) -- deliberately: the real
  smoke-test threshold is still an open design point, and this wiring alone changes nothing about
  today's live behavior. 16 new tests (6 unit + 10 integration against the real script's own
  functions via a stub `McpClient`, no live call). 504 passed total, architecture tests pass, no
  live/demo call anywhere in this increment. **Explicitly NOT done and NOT authorized**: the
  actual live smoke test run, the real threshold value, and Step 6 (demo-to-live readiness
  criteria) -- each needs its own separate, explicit go-ahead per this project's standing
  live-testing-pause rule. Full detail in `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`.
  **Step 6 (demo-to-live readiness checklist) and Step 7 (sustained forward-test run, live,
  explicit go-aheads given throughout) both under way** -- full run-by-run detail in
  `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`, not duplicated here. Step 7 has repeatedly (runs #4
  and #6, both 2026-08-10) been stopped short of its 30-cycle target by a real, twice-observed
  reconciliation gap, root-caused and STRUCTURALLY FIXED (not worked around) in this increment:
  MT5 briefly (~1 second) surfaces its own auto-generated SL/TP-close order as a live pending
  order while executing a position's stop, and `reconcile()`'s deliberately ticket-only matching
  (unchanged -- see `state/reconcile.py`'s own docstring) correctly has no way to tell that
  apart from a genuinely foreign ticket, so it trips `unknown_real` -> `MANAGE_ONLY` and halts
  the loop, exactly as designed. New `state/sl_tp_artifact.py`
  (`classify_unknown_real_tickets()`, pure, no I/O) adds a SEPARATE, second-pass evidence check
  run only against `reconcile()`'s own `unknown_real` output: a ticket is excluded only on a
  full conjunction of strong evidence (direct order->deal->position_id linkage to a position
  still in `local_open`, matching symbol/side/volume, an explicit `[sl ...]`/`[tp ...]` deal
  comment, and a price match against that position's own recorded sl/tp) -- any missing or
  ambiguous signal, or any failure gathering the evidence at all (e.g. a raised `get_deals()`
  call), leaves the ticket `unknown_real` and `MANAGE_ONLY` still trips, fail-closed, exactly as
  before. `McpOrderExecutor._explain_unknown_real()` wires this into `_current_posture()`,
  making ONE extra real `get_deals()` read, but ONLY when `unknown_real` is non-empty AND at
  least one locally-open position exists to possibly link to -- zero added cost on every normal
  cycle. An explained ticket is never adopted (no StateStore record is ever written for the
  artifact ticket itself); its underlying, already-locally-owned position IS reconciled to
  `CLOSED` automatically (`record_closed()`, local-write only, no MCP call) -- the same
  reconciliation this project always did manually after an incident of this shape, now automatic
  and evidence-backed. Reconstructed real fixtures from both incidents (tickets `171909600`/
  `171908077` from run #4, `171922069`/`171920424` from run #6 -- the latter also proving the
  price check must tolerate real stop-order fill slippage, not require an exact match) plus 18
  pure-function edge-case tests (foreign ticket, ambiguous/multiple deals, wrong side, wrong
  symbol, wrong volume, price mismatch, comment/field mismatch, pre-submission timestamp,
  `deal_time_offset` correction, idempotency) and 5 executor-level integration tests (known SL
  artifact unblocks + reconciles, known TP artifact unblocks + reconciles, real-but-insufficient
  evidence still blocks, `get_deals` failure still blocks, `record_closed()` idempotency) were
  added -- 536 passed total, architecture tests still pass. **Step 7 is PAUSED again pending this
  fix's own verification and a fresh, explicit go-ahead** -- per this project's standing
  live-testing-pause rule, this fix being merged is not itself authorization to relaunch. Full
  incident and fix detail in `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`.
  **Same day, end-of-session review**: run #7 (halted mid-cycle-3 by an explicit stop-work order,
  not a crash) reconciled clean, account confirmed flat. A dedicated false-positive-ownership
  review of `state/sl_tp_artifact.py`, tracing `_explain_one()`'s actual control flow rather than
  its docstring, confirmed: ownership is established ONLY via two exact matches against this
  project's own real deal history and local records (`deal.order == ticket`,
  `deal.position_id` in `local_open`) -- never by similarity/timing/magic; the only tolerance-based
  signal (price, ±0.2%) is evaluated strictly after every hard check already passed, so no single
  weak signal can classify a ticket alone; a genuinely foreign ticket cannot pass, structurally, no
  matter how well other fields coincidentally align, because its `position_id` was never written to
  local state by anything. Two lower-severity residual findings documented (a manual close near a
  position's own stop price could be mislabeled `sl`/`tp` in the audit trail, but this is a labeling
  risk, not an ownership risk, since the position was already known before the label is considered;
  `deal_time_offset` falls back to zero with no session reference yet, loosening the timestamp
  sanity bound only, never the ownership link). One safe, purely-additive improvement was made:
  `_explain_one()`/`classify_unknown_real_tickets()` now return a specific rejection reason per
  unexplained ticket (`SlTpArtifactRejection`), logged as a `WARNING` by
  `McpOrderExecutor._explain_unknown_real()` -- verified to change zero accept/reject outcomes (every
  pre-existing test passed unchanged before the 9 "stays unknown_real" tests were extended with
  reason assertions and 1 new partition-exhaustiveness test was added). 537 passed total,
  architecture tests still pass. Step 7 remains PAUSED, still needs its own fresh go-ahead
  tomorrow. Full detail in `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`.
- **Live pilot (symbol selection, minimum lot, initial deposit calculation, strict daily loss
  limit, limited symbols/orders, human approval before real-money execution)**: not started, not
  scoped. **Hard blocker, not just a gap**: `risk/__init__.py` already documents that margin
  guards, spread filters, and daily shutdown rules were never ported from the legacy project and
  don't exist here — there is no daily-loss-limit or kill-switch code anywhere in this codebase
  today (`pipeline/loop_control.py`'s cycle/runtime ceilings bound *time*, not *loss*). This phase
  cannot begin until that's written and tested, Phase 9 defines objective readiness criteria, and
  this doc's "No `LIVE` mode exists in this codebase" boundary (see "Execution modes" below) is
  itself explicitly revisited and approved.

## Safety rules

- Never place, modify, or close a real order. Ever, without explicit phase-gated approval.
- Never let strategy, signal, trade_intent, sizing, risk, or order_planning code call an adapter or
  an MCP tool directly. These packages must never import `execution`, `mt5_adapter`, or
  `mcp_adapter` — enforced by `tests/test_architecture.py`.
- All orders must pass through risk validation (symbol-level and portfolio-level) and order
  planning before reaching `execution`. There is no shortcut path.
- Never bypass portfolio guards, symbol guards, margin guards, spread filters, slippage validation,
  daily shutdown rules, or broker constraints (digits, point size, volume min/max/step, filling
  mode, stop level, freeze level).
- Never store API keys, passwords, account credentials, or MCP secrets in source code. Configuration
  comes from `.env` (git-ignored) via the typed settings loader in `config/`. `.env.example`
  documents variable names only.
- Do not modify `.env`, credentials, or live-trading configuration.
- Do not read or modify the legacy project's notebook credentials.
- MCP trading tools must remain disabled/unclassified until explicitly approved. `ToolRegistry`
  refuses to call anything not explicitly classified `READ_ONLY` or `TRADING`, and refuses `TRADING`
  calls unless `trading_enabled=True` — which is only ever true in `DEMO_EXECUTION` mode.
- Confirm the account is a demo account (`require_demo_account`) before any execution test, and
  before every single order-submitting call, not just once per session.
- Read-only operations must be separated from trade-execution operations at the interface level
  (`AccountReader`/`MarketDataSource` vs `OrderExecutor`).
- Every execution response must be verified against actual MT5 state (position/order lookup), not
  just the immediate return value.
- Compilation or absence of exceptions is not proof the system works. Prove it with tests and, for
  runtime behavior, with actual observed output.

## Execution modes

`READ_ONLY`, `MOCK`, `DRY_RUN`, `SHADOW`, `DEMO_EXECUTION`. No `LIVE` mode exists in this codebase.
See `README.md` and `src/mt5_mcp_trading/config/settings.py` for definitions. Mode is selected via
`MT5_MCP_MODE` in `.env`; default is `MOCK`.

## Architecture boundaries

```
market_data → features → strategy → signal → trade_intent
    → sizing → risk → order_planning → execution → mt5_adapter → mcp_adapter
```

- Everything up to and including `order_planning` is synchronous, deterministic, pure — no adapter
  references, no I/O.
- `execution`, `mt5_adapter`, `mcp_adapter` are async and are the only layers allowed to talk to the
  outside world.
- `mcp_adapter` is the only package allowed to import an MCP client library or hold an MCP
  connection.

## Testing

- `pytest`, run from the project root (`.venv/Scripts/python.exe -m pytest -v` on Windows).
- Build and test everything against mocks (`src/mt5_mcp_trading/mocks/`) before any real connection.
- `tests/test_architecture.py` must always pass — it is the enforcement mechanism for the boundary
  rules above, not just documentation of them.
- Every new adapter capability needs both a passing-case and a failure-case test (disconnection,
  invalid input, rejection) before it's considered done.
