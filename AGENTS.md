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
- **Phase 7: done.** Both scoped slices (grid_cycle failure handling, state-store-at-scale
  sweep + the O(N²) fix) are complete; user chose to close out the phase here rather than
  continue with live/MCP-adjacent failure testing or the `all_open()` per-action cost noted
  above.
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
  Full detail: `docs/PIPELINE_WIRING_CHECKPOINT.md`.

Full session-by-session detail for the "wire real adapters" step (now fully complete) is in
`docs/MCP_ADAPTER_WIRING_CHECKPOINT.md`. Phase 6 itself is tracked separately in
`docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md`, Phase 7 in
`docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md`, and pipeline wiring in
`docs/PIPELINE_WIRING_CHECKPOINT.md` — read whichever is relevant before continuing that work in
a new session.

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
