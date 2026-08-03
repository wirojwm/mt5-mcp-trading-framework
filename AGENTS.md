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

Full session-by-session detail for the "wire real adapters" step (now fully complete) is in
`docs/MCP_ADAPTER_WIRING_CHECKPOINT.md`. Phase 6 itself is tracked separately in
`docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md`, and Phase 7 in
`docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md` — read whichever is relevant before
continuing that work in a new session.

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
