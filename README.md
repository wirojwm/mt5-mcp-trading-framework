# mt5-mcp-trading

A clean-architecture rebuild of an MT5 trading system, designed from the start to talk to a
MetaTrader 5 MCP server through a single, isolated adapter boundary — never from strategy code.

This project is **not** a fork or refactor of the legacy project at
`../RealTrade/2509_17_mix_supercross`. That project is kept as a **read-only reference**: useful
business logic (grid/runner/guard math, sizing formulas, broker-constraint handling) is being
re-derived and ported deliberately, one piece at a time, with tests — not copied wholesale. See
`AGENTS.md` for the full safety rules and phase workflow this project follows.

## Status

**Phase 2 — minimal foundation.** Configuration, domain models, adapter interfaces, mock adapters,
and tests exist. There is no real MT5 or MCP connection yet, and no trading-capable code path is
reachable. Everything is built and tested against in-memory mocks only.

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
  config/        typed settings loader (env-based, no hardcoded secrets)
  domain/        frozen dataclasses shared across every layer
  market_data/   MarketDataSource interface (async)
  mt5_adapter/   AccountReader / OrderExecutor interfaces (async) + demo-account safety guard
  mcp_adapter/   tool registry: classifies MCP tools READ_ONLY vs TRADING, gates trading calls
  mocks/         in-memory implementations of the above interfaces, for tests only
  monitoring/    structured logging setup
  strategy/, signal/, trade_intent/, sizing/, risk/, order_planning/, execution/, state/, reporting/
                 intentionally empty package skeletons — populated in later phases
tests/
  unit/          pure-logic and mock-adapter tests
  test_architecture.py   import-boundary enforcement
```

## Setup

```bash
python3 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"          # macOS/Linux
```

## Running tests

```bash
.venv/Scripts/python.exe -m pytest -v
```

## Safety notes

- No code path in this repository can place, modify, or close a real order today — `OrderExecutor`
  implementations that could are not wired to anything yet, and only `MOCK` mode is exercised.
- `.env` is required to configure anything beyond defaults and is git-ignored; `.env.example`
  documents variable names only, never values.
- See `AGENTS.md` for the full set of safety rules and the phase-by-phase workflow this project
  follows before any real MT5/MCP connection, and before any demo-account execution.
