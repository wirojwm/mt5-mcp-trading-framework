# Checkpoint: wiring a real mt5_adapter / mcp_adapter implementation

Session ended here due to session-limit exhaustion, mid-step. This file is the handoff for
continuing in a new session.

## Current project goal

Rebuild the legacy `2509_17_mix_supercross` MT5 trading bot (at
`../RealTrade/2509_17_mix_supercross`, read-only reference, kept untouched throughout) as a
new clean-architecture project (`mt5_mcp_trading`) that talks to MT5 only through an isolated
MCP adapter, with strategy/risk/sizing logic kept pure and untestable-without-mocks trading
capability gated behind explicit approval. Full phase history and all safety rules are in
`AGENTS.md` and `README.md` at the project root — read those first in a new session.

## What phase this is

Not one of the original numbered phases (0–7) — this is a "wire a real adapter" step done
after Phase 5 (dry-run pipeline, built and tested against mocks only) at the user's request,
to replace the mock `MarketDataSource`/`AccountReader` with real implementations backed by the
`metatrader-mcp-server` connection already verified read-only in Phase 3.

## Work completed and verified (tests pass)

- `domain/models.py`: `AccountState.login`/`AccountState.server` changed from required to
  `Optional[...] = None` — the real `get_account_info` MCP tool exposes neither field. Same
  pattern as the earlier `TradeIntent.signal_ref` fix. **Verified**: full suite still passes
  after this change.
- `mcp_adapter/client.py` (`McpClient`): generic MCP stdio client wrapping the `mcp` SDK
  (`ClientSession` + `stdio_client` via `AsyncExitStack`). `call_tool()` calls
  `ToolRegistry.authorize_call()` before every invocation — this is the first code that
  actually enforces the registry Phase 2 built; nothing called it before this. **Not yet
  covered by a test that exercises a real or stubbed session** — see "Incomplete" below.
- `mcp_adapter/metatrader_tools.py` (`build_metatrader_tool_registry`): the real 25-tool
  classification (13 READ_ONLY, 12 TRADING) from Phase 3's live enumeration, defaulting
  `trading_enabled=False`. **Verified**: 6/6 tests pass
  (`tests/unit/test_mcp_metatrader_tools.py`).
- `mt5_adapter/metatrader_parsing.py` (`parse_iso_datetime`, `parse_dataframe_csv`): handles
  the two response shapes confirmed in Phase 3 (JSON for account/price/symbols, CSV-with-
  blank-index-column for candles/positions/orders) and the two datetime formats (`...Z` from
  `get_symbol_price`, `...+00:00` from CSV tools). **Verified**: 6/6 tests pass
  (`tests/unit/test_mt5_adapter_parsing.py`), using response text captured verbatim from the
  real Phase 3 run as fixtures, not fabricated samples.
- `mt5_adapter/mcp_market_data.py` (`McpMarketDataSource`): real `get_bars` (via
  `get_candles_latest`, re-sorted oldest-to-newest since the server returns newest-first) and
  `get_tick` (via `get_symbol_price`). `get_symbol_info()` raises `UnsupportedByServerError`
  deliberately — see "Confirmed MCP server gaps" below. **Now unit-tested** — see "Work
  completed this step" below.
- `mt5_adapter/mcp_account.py` (`McpAccountReader`): real `get_account_state`, `get_positions`,
  `get_orders`, `get_connection_state` (inferred from whether `get_account_info` succeeds, no
  dedicated tool exists). **Now unit-tested** — see "Work completed this step" below.

## Work completed this step (stub-client unit tests)

Added `tests/unit/test_mt5_adapter_mcp_market_data.py` and
`tests/unit/test_mt5_adapter_mcp_account.py`, following the plan in the previous "Exact next
smallest task" step 1. Both files define a local `_StubMcpClient` that deliberately
re-implements `McpClient.call_tool`'s one safety-critical line
(`registry.authorize_call(name)` before returning canned data) against the real, fully
classified registry from `metatrader_tools.build_metatrader_tool_registry()` — not a bypassed
mock. This means a future change that made `McpMarketDataSource` or `McpAccountReader` call an
unclassified or `TRADING`-classified tool would make these tests raise
`ToolNotClassifiedError`/`TradingDisabledError`, same as it would against a real `McpClient`.
Each file ends with a dedicated test asserting every tool call the class under test made was
classified `READ_ONLY`.

No live MCP call was made. No trading/execution tool was called or referenced (only
`READ_ONLY`-tool names appear in any fixture). `.env`/credentials were not read.

Coverage added, 30 new tests total:
- **`McpMarketDataSource`**: `get_bars` success (parses + sorts oldest-to-newest) and two
  malformed-response cases (missing `close` column → `KeyError`, non-numeric price →
  `ValueError`); `get_tick` success (Zulu-suffix timestamp) and two malformed-response cases
  (invalid JSON → `json.JSONDecodeError`, missing field → `KeyError`); `get_symbol_info` always
  raises `UnsupportedByServerError` **without making any MCP call at all** (asserted via an
  empty `client.calls`); registry-enforcement test.
- **`McpAccountReader`**: `get_account_state` success, all three valid `trade_mode` values
  (`DEMO`/`CONTEST`/`REAL`, case-insensitive), the fail-safe fallback to `"REAL"` for
  unrecognized or missing `account_type` (the safety property from gap 3 below), and two
  malformed-response cases; `get_positions`/`get_orders` success (including the `UNKNOWN_MAGIC`
  sentinel and symbol-scoped vs. all-scoped tool selection), `MagicFilteringUnavailableError`
  raised **before any MCP call** when `magic` is passed (asserted via empty `client.calls`),
  and malformed-response cases; `get_connection_state` both the inferred-success and
  inferred-failure path; registry-enforcement test.

```
pytest -q                        -> 207 passed  (177 previously + 30 new)
pytest tests/test_architecture.py -q -> 13 passed
```

## Actual MCP connection status

Not touched this session — no live call was made. The `mt5-metatrader` server registered with
Claude Code (local scope) in an earlier session should still be connected; not re-verified
here. **No live verification of the new adapter code has been performed.** All Phase 3 tool
enumeration/classification data this step relies on was already captured and is quoted
verbatim in `docs/mcp_tool_classification.md` and in the new source files' docstrings.

## Tools discovered and classification

Unchanged from Phase 3 — see `docs/mcp_tool_classification.md` for the full table. Summary:
25 real tools (not 32, per the PyPI page), 13 READ_ONLY, 12 TRADING. Now encoded as executable
classification in `mcp_adapter/metatrader_tools.py` (tested, see above) rather than only as a
markdown table.

## Confirmed MCP server gaps (found this session, safety-relevant, not yet resolved)

Found by reading `metatrader-mcp-server`'s source directly (not guessed), discussed with the
user, and handled per their explicit choices:

1. **No SymbolInfo tool exists at all** — none of the 25 tools expose
   point/digits/stops_level/freeze_level/volume_min/max/step. `McpMarketDataSource.get_symbol_info()`
   raises `UnsupportedByServerError` rather than fabricating data.
   `order_planning.build_order_plan()` cannot run against real SymbolInfo from this server —
   unresolved.
2. **No magic number (or comment) on positions/orders** — confirmed via source
   (`convert_positions_to_dataframe`/`convert_orders_to_dataframe`'s hardcoded column mapping).
   `McpAccountReader.get_positions()`/`get_orders()` raise `MagicFilteringUnavailableError` if
   `magic` is requested (user's explicit choice: fail loud, don't silently return unfiltered
   data mislabeled as filtered); when `magic=None`, returns everything with
   `PositionState.magic`/`OrderState.magic` set to sentinel `UNKNOWN_MAGIC = 0`.
3. **`account_type` field is provably inverted** (proven in Phase 3 against the raw MT5 API).
   User's explicit choice: **do not** apply the known correction (risk of silently becoming
   dangerous if the upstream bug is ever fixed). `McpAccountReader.get_account_state()` passes
   the raw value through uppercased, falling back to `"REAL"` (fail-safe/non-demo) if the
   value is missing or unrecognized. The actual demo-safety boundary for this project remains
   `MT5_ACCOUNT_KIND=DEMO` in `scripts/run_metatrader_mcp_stdio.py`, independent of anything
   read over MCP.

## Incomplete / partially implemented — do NOT treat as done

- **No unit tests yet for `McpClient` itself** — only its callers (`McpMarketDataSource`,
  `McpAccountReader`, now both tested via a stub client) and its building blocks (parsing
  helpers, tool registry) are tested. `McpClient`'s own `__aenter__`/`__aexit__`/session
  lifecycle is still exercised only by import-checking, not a real or stubbed MCP session.
  Not in scope for the step that just completed — see "Exact next smallest task" below if this
  becomes needed.
- **No live verification run** of the new adapter against the real server has been performed
  yet (deliberately deferred — the user has asked to keep this tightly scoped and not
  auto-advance to live calls; explicit approval required first, see below).
- Nothing has been committed yet this step (see git status below) — do not assume any of this
  work is saved until a commit is made.

## Current errors

None. Everything that exists compiles, imports, and passes its own tests. The gap is coverage
(untested adapter classes), not breakage.

## Safety constraints that must remain enforced (unchanged, re-affirm in new session)

- Never call a TRADING-classified MCP tool. `ToolRegistry.authorize_call()` now actually gates
  every `McpClient.call_tool()` invocation — confirm this remains true before adding anything
  new that calls tools.
- Never fabricate SymbolInfo, magic numbers, or trade_mode to route around the three gaps
  above — fail loud (raise), as already implemented.
- `.env` (real demo credentials) must never be read, logged, or displayed by any Claude Code
  action.
- Legacy project (`../RealTrade/2509_17_mix_supercross`) must remain untouched — re-verify
  `git status` there is clean before and after any further work.
- No trading/order-execution tool may be called — confirmed not done this session.

## Exact next smallest task

1. ~~Write `tests/unit/test_mt5_adapter_mcp_market_data.py` and
   `tests/unit/test_mt5_adapter_mcp_account.py` using a stub `McpClient`~~ — **done this step**,
   see "Work completed this step" above.
2. ~~Run the full suite + architecture check again.~~ — **done this step**: 207 passed, 13
   architecture tests passed.
3. ~~Update `docs/mcp_tool_classification.md` with the three confirmed gaps~~ — **done this
   step**.
4. Verify legacy repo still untouched, then commit this whole step (domain model fix + client
   + tool classification + parsing + both real adapters + all new tests, i.e. everything since
   commit `a723e0d`) as one commit, following this project's established commit-message style
   (goal, what was preserved/fixed/found, test results). **Not done — awaiting explicit
   approval, per this step's instructions to stop before any live verification; commit itself
   was not requested either.**
5. Only after that commit: propose (don't auto-run) a live, read-only verification call
   against the real server to prove `McpMarketDataSource`/`McpAccountReader` work end-to-end,
   mirroring the Phase 3 methodology — wait for explicit approval first.

## Exact prompt for continuing in a new Claude Code session

```
Continue wiring the real mt5_adapter/mcp_adapter implementation for mt5_mcp_trading.
Read docs/MCP_ADAPTER_WIRING_CHECKPOINT.md first for full context, then AGENTS.md and
README.md at the project root. Do the "Exact next smallest task" list in that checkpoint file,
in order, stopping after each numbered step to confirm before continuing. Do not call any
trading/order-execution MCP tool. Do not read, log, or display anything from .env. Verify the
legacy project at ../RealTrade/2509_17_mix_supercross remains untouched (git status clean)
before and after your work.
```
