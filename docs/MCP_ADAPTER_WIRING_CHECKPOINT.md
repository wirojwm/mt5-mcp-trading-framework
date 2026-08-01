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
  deliberately — see "Confirmed MCP server gaps" below. **Import-checked only, not
  unit-tested** — see "Incomplete".
- `mt5_adapter/mcp_account.py` (`McpAccountReader`): real `get_account_state`, `get_positions`,
  `get_orders`, `get_connection_state` (inferred from whether `get_account_info` succeeds, no
  dedicated tool exists). **Import-checked only, not unit-tested** — see "Incomplete".

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

## Tests run and results

```
pytest -q                        -> 177 passed
pytest tests/test_architecture.py -q -> 13 passed
```
Plus a bare import check of all three new adapter/client modules (no errors).

## Incomplete / partially implemented — do NOT treat as done

- **No unit tests yet for `McpClient`, `McpMarketDataSource`, or `McpAccountReader`
  themselves.** Only their building blocks (parsing helpers, tool registry) are tested. The
  planned approach (not yet executed): a fake/stub `McpClient` returning canned per-tool-name
  text, so these can be tested offline without a live connection — consistent with this
  project's "mocks first" rule.
- **No live verification run** of the new adapter against the real server has been performed
  this session (deliberately deferred — the user had already asked to keep this step tightly
  scoped and not auto-advance to live calls).
- `docs/mcp_tool_classification.md` has not yet been updated with the three gaps above (they
  are currently only documented in this checkpoint and in the new source files' docstrings).
- Nothing has been committed yet this session (see git status below) — do not assume any of
  this work is saved until a commit is made.

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

1. Write `tests/unit/test_mt5_adapter_mcp_market_data.py` and
   `tests/unit/test_mt5_adapter_mcp_account.py` using a stub `McpClient` (a small fake class
   with a `call_tool(name, arguments)` method returning canned text per tool name, matching
   the real captured samples already used in `tests/unit/test_mt5_adapter_parsing.py`) — no
   live connection. Cover: successful parse paths, `UnsupportedByServerError` for
   `get_symbol_info`, `MagicFilteringUnavailableError` when `magic` is passed, the
   trade_mode fail-safe fallback, `get_connection_state`'s inferred success/failure paths.
2. Run the full suite + architecture check again.
3. Update `docs/mcp_tool_classification.md` with the three confirmed gaps (currently only in
   this checkpoint and source docstrings).
4. Verify legacy repo still untouched, then commit this whole step (domain model fix + client
   + tool classification + parsing + both real adapters + all new tests) as one commit,
   following this project's established commit-message style (goal, what was preserved/fixed/
   found, test results).
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
