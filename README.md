# mt5-mcp-trading

A clean-architecture rebuild of an MT5 trading system, designed from the start to talk to a
MetaTrader 5 MCP server through a single, isolated adapter boundary — never from strategy code.

This project is **not** a fork or refactor of the legacy project at
`../RealTrade/2509_17_mix_supercross`. That project is kept as a **read-only reference**: useful
business logic (grid/runner/guard math, sizing formulas, broker-constraint handling) is being
re-derived and ported deliberately, one piece at a time, with tests — not copied wholesale. See
`AGENTS.md` for the full safety rules and phase workflow this project follows.

## Status

**Phases 0–5 done, real adapters wired.** The full pipeline (strategy → signal → trade_intent →
sizing → risk → order_planning → execution) is built, tested against mocks, and also live-verified
end-to-end against a real MetaTrader 5 terminal via `metatrader-mcp-server` — real market data
(bars/tick/`SymbolInfo`) and real account data (positions/orders, filtered by magic number) both
flow through `run_grid_cycle`/`run_runner_cycle` into `order_planning.build_order_plan()`
correctly. Two MCP tools missing from the upstream server (`get_symbol_info`, magic-number
filtering) were added locally rather than faked — see `AGENTS.md`'s Progress section and
`docs/MCP_ADAPTER_WIRING_CHECKPOINT.md` for the full history.

Despite all that, **no code path in this repository has ever placed, modified, or closed a real
order.** Every live run so far has gone through `DryRunExecutor`, which only records what it would
have submitted. Phase 6 (controlled demo execution) has not started.

## Architecture (summary)

```
market_data → features → strategy → signal → trade_intent
    → sizing → risk (symbol + portfolio) → order_planning → execution → mt5_adapter → mcp_adapter
```

`strategy`, `signal`, `trade_intent`, `sizing`, `risk`, and `order_planning` are pure, synchronous,
deterministic, and **never import** `execution`, `mt5_adapter`, or `mcp_adapter`. That boundary is
enforced by `tests/test_architecture.py`, not just documentation. `mt5_adapter` and `mcp_adapter`
are async; everything upstream of them is not.

## Directory layout

```
src/mt5_mcp_trading/
  config/          typed settings loader (env-based, no hardcoded secrets)
  domain/          frozen dataclasses shared across every layer
  market_data/     MarketDataSource interface (async) -- real implementation lives in mt5_adapter/
  features/        pure indicator functions: EMA, ATR, MACD
  strategy/        grid levels, runner signal, EMA exit-guard state machine -- pure
  signal/          empty; Signal is a domain/models.py dataclass, no package-specific code yet
  trade_intent/    GridLevels/Signal -> TradeIntent conversion
  sizing/          decide_lot -- fixed/atr_scale/risk_percent lot sizing
  risk/            symbol-level + portfolio-level guards, and combine() to merge decisions
  order_planning/  build_order_plan(): volume clamping + LIMIT price normalization against real
                   SymbolInfo/broker constraints
  execution/       DryRunExecutor -- OrderExecutor that logs and records, never calls MCP/MT5
  pipeline/        run_grid_cycle()/run_runner_cycle(): one full strategy->execution pass each,
                   constructor-injected with whichever MarketDataSource/AccountReader/
                   OrderExecutor (mock or real) the caller provides
  mt5_adapter/     AccountReader/OrderExecutor interfaces (async) + demo-account safety guard,
                   PLUS the real McpMarketDataSource/McpAccountReader implementations and MT5
                   response parsing (mcp_market_data.py, mcp_account.py, metatrader_parsing.py)
  mcp_adapter/     McpClient (the only thing that ever holds an MCP session) + ToolRegistry:
                   classifies MCP tools READ_ONLY vs TRADING and gates trading calls
  mocks/           in-memory implementations of the above interfaces, for tests only
  monitoring/      structured logging setup
  state/, reporting/
                   intentionally empty package skeletons -- populated in later phases
tests/
  unit/            pure-logic, mock-adapter, and stub-MCP-client tests
  integration/     end-to-end pipeline tests (run_grid_cycle/run_runner_cycle against mocks)
  test_architecture.py   import-boundary enforcement
scripts/
  run_metatrader_mcp_stdio.py         launches the MCP server subprocess, credentials from .env
  metatrader_mcp_extended_server.py   local MCP server extension: adds get_symbol_info,
                                       get_positions_with_magic, get_pending_orders_with_magic
                                       (missing upstream) alongside the third-party package's
                                       own 25 tools -- not a fork, see its module docstring
  verify_mcp_adapters_readonly.py,
  phase3_readonly_verification.py     live, read-only verification against the real server
  run_live_dry_run_pipeline.py        live dry-run of the full pipeline against real adapters
docs/
  MCP_ADAPTER_WIRING_CHECKPOINT.md    full session-by-session history of wiring real adapters
  mcp_tool_classification.md          the real MCP tool surface, gaps found, and fixes applied
```

## Setup

This project targets **Python 3.12 (64-bit) exactly** on both development machines (home and
work) -- see `.python-version` and `requires-python` in `pyproject.toml`.
`metatrader-mcp-server==0.5.1` requires Python >=3.10, so Python 3.9 is not a supported
runtime here even though it may be present on a machine for other projects. If you run
`pytest` (or import `mt5_mcp_trading`) under anything older than 3.12, it fails immediately
with a clear error rather than a confusing dependency or syntax error further in.

1. **Install Python 3.12 64-bit** if it isn't already on the machine. On Windows, the
   [py launcher](https://docs.python.org/3/using/windows.html#launcher) makes it easy to have
   multiple versions installed side by side; confirm 3.12 is available with `py -0p`.

2. **Create a fresh virtual environment** in the project root, using Python 3.12 specifically
   (don't let this pick up whatever `python` resolves to on the machine):

   ```bash
   py -3.12 -m venv .venv          # Windows, via the py launcher
   # python3.12 -m venv .venv      # macOS/Linux
   ```

3. **Install dependencies** -- the project package in editable mode, plus everything pinned
   in `requirements-dev.txt` (which itself pulls in `requirements.txt`):

   ```bash
   .venv/Scripts/python.exe -m pip install --upgrade pip
   .venv/Scripts/python.exe -m pip install -e . -r requirements-dev.txt   # Windows
   # .venv/bin/python -m pip install --upgrade pip                       # macOS/Linux
   # .venv/bin/python -m pip install -e . -r requirements-dev.txt
   ```

4. **Create `.env` from `.env.example`** and fill in machine-specific values. `.env` is
   git-ignored and must never be committed:

   ```bash
   cp .env.example .env   # Windows: copy .env.example .env
   ```

5. **Configure `MT5_PATH` locally** in your `.env` (not `.env.example`) to this machine's
   `terminal64.exe` path -- it differs between the home and work machines whenever the
   installed terminal build or broker differs. See the comment above `MT5_PATH` in
   `.env.example` for why this is needed.

6. **Run the test suite before starting development**, to confirm the environment is sound
   on this machine:

   ```bash
   .venv/Scripts/python.exe -m pytest -v
   ```

## Running tests

```bash
.venv/Scripts/python.exe -m pytest -v
```

## Safety notes

- No code path in this repository can place, modify, or close a real order today — the only
  `OrderExecutor` wired to anything reachable is `DryRunExecutor`, which logs and records but never
  calls MCP or MT5. `MOCK` and `DRY_RUN` (the latter against real adapters/a real MT5 connection,
  see Status above) have both been exercised; no `OrderExecutor` capable of a real order exists yet.
- `.env` is required to configure anything beyond defaults and is git-ignored; `.env.example`
  documents variable names only, never values.
- See `AGENTS.md` for the full set of safety rules and the phase-by-phase workflow this project
  follows before any real MT5/MCP connection, and before any demo-account execution.
