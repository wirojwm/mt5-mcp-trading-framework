# mt5-mcp-trading

A clean-architecture rebuild of an MT5 trading system, designed from the start to talk to a
MetaTrader 5 MCP server through a single, isolated adapter boundary — never from strategy code.

This project is **not** a fork or refactor of the legacy project at
a private sibling location. That project is kept as a **read-only reference**: useful
business logic (grid/runner/guard math, sizing formulas, broker-constraint handling) is being
re-derived and ported deliberately, one piece at a time, with tests — not copied wholesale. See
`AGENTS.md` for the full safety rules and phase workflow this project follows.

## Status

**Research and engineering framework, demo-account only. Not a production trading system, and no
profitability is claimed.** Phases 0-6 (legacy audit, design, foundation, read-only MCP integration,
strategy migration, dry-run pipeline, controlled demo execution) are complete, as are the
real-adapter wiring and pipeline-wiring efforts between them. Phase 7 (regression and failure
testing) is closed only for its chosen scope: some failure modes, such as an order lost in flight
during a disconnect, are explicitly accepted as untested open risks. Phase 8
(strategy research) and Phase 9 (demo forward test) have produced documented operational
milestones and, for the strategies examined, **negative or null research results** (see below).
The live-pilot phase has **not started** and is blocked. `AGENTS.md` is the authoritative
phase-by-phase record; `docs/*_CHECKPOINT.md` hold the detail.

### Execution levels

| Level | What it means here | Status |
|---|---|---|
| Mock | In-memory adapters, no network, no MT5/MCP connection | Implemented, covered by the offline test suite |
| Dry-run | Real reads, an `OrderPlan` is built and logged, no trading tool is ever called (`DryRunExecutor`) | Implemented, exercised against a real MT5 terminal via read-only calls |
| Controlled demo | `McpOrderExecutor` places/modifies/cancels/closes orders through the MCP server; gated to `DEMO_EXECUTION` mode and an operator-declared demo account (see Safety notes) | Implemented; exercised in explicitly approved, supervised runs on a broker demo account (recorded in the checkpoint docs) |
| Live account | Real-money execution | **Not supported.** No `LIVE` execution mode exists (`ExecutionMode` defines only `READ_ONLY`, `MOCK`, `DRY_RUN`, `SHADOW`, `DEMO_EXECUTION`). Not validated, not authorized. |

### Research verdicts

All findings below come from this repository's own backtests and statistical tests on cached
BTCUSD and XAUUSD history from one broker demo environment, using a spread-only cost model. They
are scoped to the instruments, timeframes, windows and rules actually tested. **No signal or
strategy examined here is validated as a profitable edge**, and these results do not show that no
edge exists elsewhere.

- **Grid (BTCUSD M1, ~67 days of cached history, train/held-out split):** negative expectancy at
  default parameters. A tuned candidate (`step_mult=0.25`) did not hold up on the held-out
  window. A regime-filter effort was closed as a negative result: of two candidate thresholds, one
  reversed on the held-out window and the other improved expectancy but sharply worsened
  drawdown. On XAUUSD M1 (three ~31-day windows) the unmodified grid was also consistently
  negative.
- **Runner (BTCUSD M1):** negative expectancy. Its MACD-sign signal did not beat a same-cost
  random-direction baseline in any of three windows (the differences were not statistically
  significant, so this is "no demonstrated edge", not proof of a negative one). A parameter change
  that validated on a held-out window was later found not to be a valid comparator for the
  currently configured strategy. On XAUUSD M1 the runner produced only 1-4 trades per window, so
  that test was **inconclusive**.
- **BTCUSD M1 price action itself:** mixed and unstable across windows; the only consistent
  finding was a one-bar effect described in the docs as economically negligible.
- **BTCUSD M15/H1 one-bar mean reversion:** a statistically consistent one-bar structure was
  found (classified "B" by the project's own rubric), but it was economically negligible. A naive
  rule built on it ("fade the last bar", one-bar hold) had negative expectancy after spread costs
  in all six tested timeframe/window cells, and the investigation recommended abandoning that
  rule. Other rule shapes around the same structure were not tested.
- **XAUUSD:** the M1 and M15/H1 structure tests were weaker and less consistent than BTCUSD's
  (classified "C"), and no cost-test harness was built because the outcome was judged a
  foregone conclusion. The thread was closed with a recommendation to abandon.
- **Session / weekday seasonality (BTCUSD and XAUUSD M1):** mean return did not vary
  significantly by session or weekday in any window; volatility clustering was found but is not a
  directional signal. Recommended to abandon as a directional-edge lead.
- **Demo forward test (BTCUSD):** recorded expectancy was negative for both strategies. The runner
  sample blended three different parameter configurations, so it is not a clean estimate of the
  current configuration. The Phase 9 readiness checklist deliberately does not gate on positive
  edge and records that strategy-edge readiness has **not** been shown.

### Testing and validation provenance

- The automated suite (`tests/`) is designed to run without a broker connection, credentials, an
  `.env` file or a MetaTrader 5 terminal, using mocks and a local stub server (see Running tests).
  Documentation in this repository records historical passing runs (for example, 556 tests on an
  earlier development checkout). **Those are historical results; they have not been re-run
  against this published commit, and there is no CI configured in this repository.**
- Demo-account observations in the checkpoint docs are historical operator-supervised runs on one
  broker demo environment. They are not evidence of current runtime behavior, of performance on
  any other broker or symbol, or of readiness for a live account.

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
  features/        pure indicator functions: EMA, ATR, MACD, efficiency ratio
  strategy/        grid levels, runner signal, EMA exit-guard state machine -- pure (the
                   exit-guard is implemented and unit-tested but not wired into the pipeline)
  signal/          intentionally empty; Signal is a domain/models.py dataclass
  trade_intent/    GridLevels/Signal -> TradeIntent conversion
  sizing/          decide_lot -- fixed/atr_scale/risk_percent lot sizing
  risk/            symbol-level + portfolio-level guards, a daily-loss kill-switch guard, and
                   combine() to merge decisions (no margin or spread-filter guard exists yet)
  order_planning/  build_order_plan(): volume clamping + LIMIT price normalization against real
                   SymbolInfo/broker constraints
  execution/       DryRunExecutor (logs and records, never calls MCP/MT5) and composition.py, the
                   intended single construction point for a trading-capable session (DEMO_EXECUTION only)
  pipeline/        run_grid_cycle()/run_runner_cycle(): one full strategy->execution pass each,
                   constructor-injected with whichever MarketDataSource/AccountReader/
                   OrderExecutor (mock or real) the caller provides; loop_control.py holds the
                   bounded-loop stop rules
  mt5_adapter/     AccountReader/OrderExecutor interfaces (async) + demo-account safety guards,
                   the real McpMarketDataSource/McpAccountReader/McpDealHistoryReader, MT5 response
                   parsing, and McpOrderExecutor (the only order-capable executor; demo only)
  mcp_adapter/     McpClient (the only thing that ever holds an MCP session) + ToolRegistry:
                   classifies MCP tools READ_ONLY vs TRADING and gates trading calls
  mocks/           in-memory implementations of the above interfaces, for tests only
  state/           local order-state store and reconciliation against live MT5 state
  backtest/        offline replay engine, ledger, metrics, and market-data cache
  monitoring/      structured logging and demo performance monitoring
  reporting/       intentionally empty package skeleton
tests/
  unit/            pure-logic, mock-adapter, and stub-MCP-client tests
  integration/     end-to-end pipeline tests (run_grid_cycle/run_runner_cycle against mocks)
  test_architecture.py   import-boundary enforcement
scripts/
  run_metatrader_mcp_stdio.py         launches the MCP server subprocess, credentials from .env
  metatrader_mcp_extended_server.py   local MCP server extension: adds get_symbol_info,
                                       get_positions_with_magic, get_pending_orders_with_magic
                                       (missing upstream) alongside the third-party package's
                                       own tools -- not a fork, see its module docstring
  verify_mcp_adapters_readonly.py,
  phase3_readonly_verification.py     live, read-only verification against the real server
  run_live_dry_run_pipeline.py        live dry-run of the full pipeline against real adapters
  run_demo_execution_*.py             operator scripts for a connected DEMO account. Many of them
                                       can place, modify, cancel, or close real demo orders; others
                                       are read-only checks or offline research. Read a script
                                       before running it. None is needed to run the test suite.
docs/
  *_CHECKPOINT.md                     per-phase / per-effort history, decisions, and verdicts
  DEMO_TO_LIVE_READINESS_CHECKLIST.md planning input only; not an authorization
  STRATEGY_VALIDATION_FRAMEWORK.md    the validation discipline this repository practiced
  mcp_tool_classification.md          the real MCP tool surface, gaps found, and fixes applied
```

## Setup

This project targets **Python 3.12 (64-bit) on Windows** -- see `.python-version` and
`requires-python` in `pyproject.toml`. `requirements.txt` installs `MetaTrader5`, which is
Windows-only, and a real MetaTrader 5 terminal is needed only for the broker-connected scripts, not
for the offline tests. Running `pytest` (or importing `mt5_mcp_trading`) under anything other than
3.12 fails immediately with a clear error.

1. **Install Python 3.12 64-bit** if it isn't already on the machine. The
   [py launcher](https://docs.python.org/3/using/windows.html#launcher) makes it easy to have
   multiple versions installed side by side; confirm 3.12 is available with `py -0p`.

2. **Create a fresh virtual environment** in the project root, using Python 3.12 specifically
   (don't let this pick up whatever `python` resolves to on the machine):

   ```bash
   py -3.12 -m venv .venv
   ```

3. **Install dependencies** -- the project package in editable mode, plus everything pinned
   in `requirements-dev.txt` (which itself pulls in `requirements.txt`):

   ```bash
   .venv/Scripts/python.exe -m pip install --upgrade pip
   .venv/Scripts/python.exe -m pip install -e . -r requirements-dev.txt
   ```

4. **(Broker-connected scripts only) create `.env` from `.env.example`** and fill in
   machine-specific values. Use a **demo** account only. `.env` is git-ignored and must never be
   committed:

   ```bash
   cp .env.example .env   # Windows: copy .env.example .env
   ```

5. **(Broker-connected scripts only) configure `MT5_PATH` locally** in your `.env` (not `.env.example`) to this machine's
   `terminal64.exe` path -- it differs whenever the installed terminal build or broker differs. See the comment above `MT5_PATH` in
   `.env.example` for why this is needed.

6. **Run the offline test suite before starting development**, to confirm the environment is
   sound on this machine:

   ```bash
   .venv/Scripts/python.exe -m pytest -v
   ```

## Running tests

```bash
.venv/Scripts/python.exe -m pytest -v
```

This runs the suite under `tests/`, which is written to use mocks and stubs only: the tests
construct executors with mock or stub clients, and their docstrings state that no credentials,
`.env`, MT5 terminal or live call is involved. A few integration tests start a local throwaway stub
MCP server as a subprocess (no network), and a few import one operational script as a module to
test its helper functions without calling its `main()`. The suite is not meant to run any
`scripts/run_demo_execution_*.py` script. This is based on reading the test code, not on a fresh
run: results for this commit have not been re-verified, so no pass count is given here.
`tests/test_architecture.py` enforces the import boundaries above.

## Safety notes

- Order-capable code exists. `McpOrderExecutor` (`mt5_adapter/mcp_order_executor.py`) can place,
  modify, cancel, and close orders through the MCP server. A trading-capable session is built
  only through `execution/composition.py` (`demo_execution_session()`), which refuses unless
  `MT5_MCP_MODE=DEMO_EXECUTION` **and** the environment variable `MT5_ACCOUNT_KIND` is exactly
  `DEMO`. The executor re-validates that declared value (captured when it was constructed)
  before each submit, cancel, or close. The default mode is `MOCK`.
- **The demo gate is an operator declaration, not a verification.** `MT5_ACCOUNT_KIND=DEMO` is a
  value the operator writes in `.env`; no code in this repository independently confirms with the
  broker that the connected account is a demo account. The account type reported over MCP is
  checked too, but only as an informational warning that does not block (the upstream server's
  reported value was found to be unreliable). Using a real-money login while declaring `DEMO`
  would not be stopped by these checks, so only demo credentials should ever be configured.
- **No live-account support exists.** There is no `LIVE` mode, and nothing here has been validated
  or authorized for real money.
- Missing guards: margin guards and spread filters are not implemented (see `risk/`).
- A third-party MCP server (`metatrader-mcp-server`) with trading tools is part of the setup.
  Installing it does not by itself enable trading in this codebase, but running the
  broker-connected scripts against a connected demo account can submit demo orders.
- `.env` is required to configure anything beyond defaults and is git-ignored; `.env.example`
  documents variable names only, never values.
- See `AGENTS.md` for the full set of safety rules and the phase-by-phase workflow this project
  follows before any real MT5/MCP connection, and before any demo-account execution.
