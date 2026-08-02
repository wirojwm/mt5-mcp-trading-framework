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
- **Phase 6 (controlled demo execution): in progress.** No code path has ever placed, modified,
  or closed a real order yet — `McpOrderExecutor`, the first `OrderExecutor` implementation
  that could, exists but has never made a live call. Planned (via Claude Code plan mode) and
  implemented so far, in small individually-tested steps:
  - Found two blocking safety problems before writing any executor code: (1) `require_demo_account()`
    is unreliable when fed by `McpAccountReader` (the same `account_type` inversion bug as
    elsewhere) — fixed with a second, reliable, env-sourced hard gate,
    `require_demo_account_kind()`. (2) `metatrader_client`'s own `send_order()` silently drops
    `magic` (and forces `comment`, ignores `deviation`/`filling_mode`/`expiry`, and — worst —
    determines success from a terminal-level error code rather than the broker's real
    `retcode`) — deliberately NOT fixed by writing new order-placement code (highest-risk code
    this project could contain); instead the `state/` package now tracks intended
    magic/comment/strategy locally, reconciled against real MT5 state by ticket only, with an
    explicit `NORMAL`/`MANAGE_ONLY`/`BLOCKED` posture that refuses new orders (or even
    management of unattributed tickets) whenever local state can't be trusted.
  - `McpOrderExecutor` built and unit-tested for LIMIT-order `submit()`/`cancel()` only
    (`close_position()` and MARKET orders explicitly raise `NotImplementedError`, deferred).
    Retcode read from the raw response only, never the tool's own success flag. Composition
    root (`execution/composition.py`) is the one place `trading_enabled=True` is ever
    constructed outside a test.
  - **Not yet done**: any live call at all. The next step (place-then-cancel a single
    far-from-market LIMIT order) needs its own explicit approval before running, per this
    project's established practice.
  - Full detail: `docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md`.
- **Phase 7 (regression and failure testing): not started.**

Full session-by-session detail for the "wire real adapters" step (now fully complete) is in
`docs/MCP_ADAPTER_WIRING_CHECKPOINT.md`. Phase 6 itself is tracked separately in
`docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md` — read whichever is relevant before
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
