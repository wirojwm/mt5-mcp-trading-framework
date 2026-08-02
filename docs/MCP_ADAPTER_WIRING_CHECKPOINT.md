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
  `get_tick` (via `get_symbol_price`). `get_symbol_info()` now calls a locally-added
  `get_symbol_info` tool and parses real broker specs — see "Work completed this step (real
  SymbolInfo)" below; previously raised `UnsupportedByServerError` (gap 1, now resolved).
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

**Live-verified this step.** Added `scripts/verify_mcp_adapters_readonly.py` (mirrors
`scripts/phase3_readonly_verification.py`'s methodology — same wrapper launch, same
never-touch-`.env` discipline — but goes through `McpMarketDataSource`/`McpAccountReader`
themselves instead of a raw `ClientSession`) and ran it against the live server with explicit
user approval. Results:

- Connected successfully via the same wrapper Claude Code uses.
- **Live proof that trading is blocked**: `client.call_tool("place_market_order", ...)` was
  refused by `TradingDisabledError` before any RPC was sent to the server — confirmed no
  network call for that tool happened at all (raised synchronously inside
  `McpClient.call_tool()`, before the `await session.call_tool(...)` line).
- `get_connection_state()` → `connected=True`.
- `get_account_state()` → real balance/equity/margin_free returned; `trade_mode='REAL'`. This
  is the account_type-inversion bug (gap 3) manifesting exactly as documented — not a new bug.
- `require_demo_account()` → raised `NotDemoAccountError` as a direct consequence of the above.
  Expected and consistent with this project's explicit choice not to correct the inversion;
  the real demo-safety boundary (`MT5_ACCOUNT_KIND=DEMO` in
  `scripts/run_metatrader_mcp_stdio.py`) is what actually gated this connection, independent
  of this field.
- `get_bars("BTCUSD", "M1", count=5)` → 5 real candles, correctly parsed and sorted
  oldest-to-newest.
- `get_tick("BTCUSD")` → real bid/ask/time, correctly parsed.
- `get_symbol_info("BTCUSD")` → raised `UnsupportedByServerError` as designed (gap 1).
- `get_positions()` / `get_orders()` → both empty (no open positions/pending orders on this
  account), parsed without error.
- `get_positions(magic=1)` → raised `MagicFilteringUnavailableError` as designed (gap 2).
- No trading/order-submitting tool was ever sent over the wire, confirmed by the script's own
  final line and by `ToolRegistry`'s live enforcement above.

All Phase 3 tool enumeration/classification data this step relies on was already captured and
is quoted verbatim in `docs/mcp_tool_classification.md` and in the new source files'
docstrings.

## Work completed this step (real SymbolInfo via a locally-added MCP tool)

Resolved gap 1 (below): decided, with the user, to source real `SymbolInfo` by adding one
read-only MCP tool locally rather than hardcoding broker specs. Finding that made this cheap:
`metatrader-mcp-server`'s own dependency, `metatrader_client`, already implements
`market.get_symbol_info(symbol_name)` correctly (wraps `MetaTrader5.symbols_get()`, returns
the full raw `SymbolInfo` dict) — `metatrader_mcp/server.py` just never registers it with
`@mcp.tool()`, the same class of bug as its already-documented `get_candles_by_date` gap.

- `scripts/metatrader_mcp_extended_server.py` (new): imports `metatrader_mcp.server`'s
  already-built `mcp` `FastMCP` instance and `get_client`/`Context` helpers **unmodified** (no
  fork, no vendored copy), registers one additional `get_symbol_info` tool wrapping
  `client.market.get_symbol_info(...)`, then launches the same way that module's own
  `__main__` block does (identical argv interface). Hit and fixed one real bug while wiring
  this: this file must NOT use `from __future__ import annotations` — FastMCP's
  `Tool.from_function()` does `issubclass(param.annotation, Context)` on the raw annotation at
  import time, and postponed evaluation turns that into the string `"Context"`, raising
  `TypeError`. Confirmed by actually hitting it, not guessed; documented inline in the file so
  a future "consistency" cleanup doesn't reintroduce it. **Verified**: imported directly and
  called `mcp.list_tools()` — 26 tools registered (the 25 upstream + `get_symbol_info`).
- `scripts/run_metatrader_mcp_stdio.py`: now launches `metatrader_mcp_extended_server.py` (via
  `sys.executable`) instead of the pip-installed `metatrader-mcp-server` console script.
  Removed `_find_console_script()` (dead code once the console script is no longer launched).
  Credential handling itself is unchanged — same env vars, same explicit `--login`/
  `--password`/`--server`/`--path`/`--transport` CLI args passed to the child process.
- `mcp_adapter/metatrader_tools.py`: added `get_symbol_info` to `READ_ONLY_TOOLS` (it only
  reads `MetaTrader5.symbols_get()`, no write path of any kind) — 14 READ_ONLY, 12 TRADING, 26
  total.
- `mt5_adapter/metatrader_parsing.py`: added `parse_symbol_filling_modes(bitmask)`, decoding
  MQL5's documented `ENUM_SYMBOL_FILLING_MODE` bitmask (`SYMBOL_FILLING_FOK=1`,
  `SYMBOL_FILLING_IOC=2`) into a `tuple[str, ...]`. Unknown bits are kept as an explicit
  `"UNKNOWN_BIT_<n>"` entry rather than silently dropped.
- `mt5_adapter/mcp_market_data.py`: `get_symbol_info()` now calls the new tool and maps raw MT5
  field names (`name`, `digits`, `point`, `volume_min`, `volume_max`, `volume_step`,
  `trade_stops_level`, `trade_freeze_level`, `filling_mode`, `spread`) onto this project's
  `SymbolInfo` domain model. **This field-name mapping is based on documented MetaTrader5
  `SymbolInfo` fields, not yet confirmed against a live capture** — see "Exact next smallest
  task" below.
- Tests updated/added (7 new, all passing against a stub client — no live call): success parse
  in `test_mt5_adapter_mcp_market_data.py` (replaces the old always-raises test), two
  malformed-response cases, 5 cases for `parse_symbol_filling_modes` in
  `test_mt5_adapter_parsing.py`, updated tool-count assertions and the "unknown tool" fixture
  in `test_mcp_metatrader_tools.py` (now `get_terminal_info`, since `get_symbol_info` is
  classified now).
- `docs/mcp_tool_classification.md` updated: gap 6 marked resolved (locally, not upstream),
  tool counts corrected to 26 everywhere, new tool listed in the symbols/market-data table.
- `scripts/verify_mcp_adapters_readonly.py` updated to call the real `get_symbol_info()` and
  print its result instead of expecting it to raise.

```
pytest -q                        -> 214 passed  (207 previously + 7 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No live MCP call was made building this (import-checking the extended server module and
running the local test suite only). Live-verified afterward, with explicit user approval --
see "Actual MCP connection status (SymbolInfo live verification)" below. No trading/execution
tool was called or referenced. `.env`/credentials were not read.

## Actual MCP connection status (SymbolInfo live verification)

**Live-verified.** Re-ran `scripts/verify_mcp_adapters_readonly.py` (now calling the real
`get_symbol_info()` instead of expecting it to raise) against the live server, with explicit
user approval. Confirms `scripts/metatrader_mcp_extended_server.py` actually connects (not
just import-checked) and that the documented field-name mapping was correct on the first try:

```
get_symbol_info('BTCUSD') ->
SymbolInfo(symbol='BTCUSD', digits=2, point=0.01, volume_min=0.01, volume_max=5.0,
           volume_step=0.01, stops_level=10, freeze_level=0, filling_modes=('FOK',), spread=1500)
```

No `KeyError` (every raw field name used — `name`, `digits`, `point`, `volume_min`,
`volume_max`, `volume_step`, `trade_stops_level`, `trade_freeze_level`, `filling_mode`,
`spread` — was present in the live response) and no `UNKNOWN_BIT_*` in `filling_modes` (this
symbol reports only bit 1 / FOK; bit 2 / IOC was not exercised by this account's symbol, so
that half of the bitmask mapping has plausible-but-not-fully-exercised confirmation — worth
re-checking against a symbol/account that supports IOC if one becomes available, not blocking
otherwise). All other checks (trading blocked pre-RPC, connection state, account state,
`get_bars`/`get_tick`, positions/orders, magic-filtering refusal) repeated cleanly, consistent
with the prior live-verification pass. No trading/order-submitting tool was ever sent over the
wire.

This closes out gap 1 fully: implemented, unit-tested, and now live-verified.

## Tools discovered and classification

Upstream unchanged from Phase 3 — see `docs/mcp_tool_classification.md` for the full table:
25 real tools (not 32, per the PyPI page), 13 READ_ONLY, 12 TRADING. This project's own server
process now exposes **26**: the 25 upstream plus a locally-added `get_symbol_info` (14
READ_ONLY, 12 TRADING total) — see "Work completed this step (real SymbolInfo)" above. Encoded
as executable classification in `mcp_adapter/metatrader_tools.py` (tested, see above) rather
than only as a markdown table.

## Confirmed MCP server gaps (found this session, safety-relevant, not yet resolved)

Found by reading `metatrader-mcp-server`'s source directly (not guessed), discussed with the
user, and handled per their explicit choices:

1. ~~**No SymbolInfo tool exists at all**~~ — **fully resolved**, locally rather than upstream:
   see "Work completed this step (real SymbolInfo)" and "Actual MCP connection status
   (SymbolInfo live verification)" above. None of `metatrader-mcp-server`'s own 25 tools expose
   point/digits/stops_level/freeze_level/volume_min/max/step, but its dependency
   `metatrader_client` already implements this correctly; `scripts/metatrader_mcp_extended_server.py`
   registers it as a 26th tool. `McpMarketDataSource.get_symbol_info()` now calls it instead of
   raising `UnsupportedByServerError`, and this has been confirmed live against a real BTCUSD
   symbol (implemented, unit-tested, live-verified).
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
- **`filling_modes`'s IOC bit (2) has not been exercised live**, only FOK (1) — see "Actual MCP
  connection status (SymbolInfo live verification)" above. Not blocking, but worth re-checking
  against a symbol/account that reports IOC support if one becomes available.
- Nothing has been committed yet this step (see git status below) — do not assume any of this
  work is saved until a commit is made.

## Work completed this step (wire order_planning.build_order_plan() against real SymbolInfo)

Goal: prove `build_order_plan()` runs correctly against real, live `SymbolInfo`/`Tick`/
`MarketBar` data, not mock/fabricated values. Found a real blocking issue while doing this,
resolved by explicit user decision rather than silently worked around:

**Blocker found**: `run_grid_cycle`/`run_runner_cycle` (the pipeline functions that call
`build_order_plan()`) both call `account.get_positions(symbol=symbol, magic=magic)`/
`get_orders(...)` with a real magic number, for duplicate-order and exposure-cap checks. The
real `McpAccountReader` raises `MagicFilteringUnavailableError` whenever `magic` is not `None`
(gap 2 -- the MCP server exposes no magic number at all). So `run_grid_cycle`/`run_runner_cycle`
cannot run against a real `McpAccountReader` at all today. **User's explicit choice**: keep
this step scoped to SymbolInfo only -- use `MockAccountReader` (zero positions/orders) for the
account side rather than also fixing the magic-filtering gap in pipeline code (a separate,
bigger, riskier change to core risk-guard logic used by 4 existing tests). Documented in the
new script as a scope note, not a silent workaround.

- `scripts/run_live_dry_run_pipeline.py` (new): connects with the default `ToolRegistry`
  (`trading_enabled=False`), uses the real `McpMarketDataSource` for bars/tick/`SymbolInfo`,
  `MockAccountReader` (zero positions/orders) for the account side, and `DryRunExecutor`
  (never calls MCP/MT5 -- see `execution/dry_run.py`) for the executor. Runs one
  `run_grid_cycle` and one `run_runner_cycle` pass for BTCUSD and prints whatever
  `build_order_plan()` produced. Also repeats the live TRADING-tool-refused-before-any-RPC
  safety check from `scripts/verify_mcp_adapters_readonly.py`.
- No changes needed in `order_planning/`, `pipeline/`, or the `MarketDataSource`/
  `McpMarketDataSource` code itself -- the real `get_symbol_info()` added in the previous step
  already satisfies exactly what `run_grid_cycle`/`run_runner_cycle` expect.

**Live-verified**, with explicit user approval. Results:

- Trading blocked pre-RPC, confirmed again (same check as before).
- `run_grid_cycle('BTCUSD')` → two `ExecutionResult`s: BUY LIMIT @ 62903.91 and SELL LIMIT @
  62942.94, both 0.01 lots. Volume correctly clamped/rounded against the live
  `volume_min=0.01`/`volume_step=0.01` captured in the previous step's live check. Both LIMIT
  prices normalized successfully against real `point`/`stops_level`/`freeze_level` (neither
  returned `None`, which would mean "couldn't push the price far enough from market").
- `run_runner_cycle('BTCUSD')` → one `ExecutionResult`: SELL MARKET @ 62927.79, 0.01 lots.
- All three: `broker_comment='mock'`, `verification_details='MockOrderExecutor - no real order
  was placed'` — every plan went to `DryRunExecutor` only. No order was ever submitted
  anywhere, real or otherwise, and no trading tool was ever sent over the wire.

This is the first time `build_order_plan()` has run against real (not hardcoded-in-a-test)
`SymbolInfo`, and it produced sane, correctly-normalized `OrderPlan`s on the first try.

```
pytest -q                        -> 214 passed (unchanged -- no source under test/ changed)
pytest tests/test_architecture.py -q -> 13 passed
```

Nothing committed yet this step (see git status below).

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
5. ~~Only after that commit: propose (don't auto-run) a live, read-only verification call
   against the real server~~ — **done this step**, with explicit approval: see "Actual MCP
   connection status" above. `scripts/verify_mcp_adapters_readonly.py` is not yet committed.

## Exact next smallest task (after this step)

1. ~~Verify legacy repo still untouched, then commit `scripts/verify_mcp_adapters_readonly.py`
   plus this checkpoint's live-verification update as one commit~~ — **done** (that commit
   landed before this SymbolInfo step started).
2. ~~Decide how to source real `SymbolInfo`~~ — **done**: locally-added `get_symbol_info` tool,
   implemented and unit-tested this step (see "Work completed this step (real SymbolInfo)"
   above).
3. ~~Propose (don't auto-run) a live, read-only verification call exercising the extended
   server end-to-end~~ — **done**, with explicit approval: see "Actual MCP connection status
   (SymbolInfo live verification)" above. Field-name mapping confirmed correct on the first
   try; no fix needed.
4. ~~Verify legacy repo still untouched, then commit this whole step~~ — **done** (that commit,
   `0dc6e44`, landed before this "wire build_order_plan against real SymbolInfo" step started).
5. ~~`order_planning.build_order_plan()` wired against real `SymbolInfo` for an end-to-end
   dry-run against the live server~~ — **done this step**, with explicit approval: see "Work
   completed this step (wire order_planning.build_order_plan() against real SymbolInfo)"
   above. `scripts/run_live_dry_run_pipeline.py` is not yet committed.

## Exact next smallest task (after this step)

1. Verify legacy repo still untouched, then commit `scripts/run_live_dry_run_pipeline.py` plus
   this checkpoint's update as one commit — wait for explicit approval first (not yet requested
   as of this writing).
2. The still-open item is gap 2 (magic-number filtering): `run_grid_cycle`/`run_runner_cycle`
   cannot run against a real `McpAccountReader` today, only `MockAccountReader`. Decide, with
   the user, whether/how to address this (e.g. change the pipeline's duplicate-order/exposure
   checks to tolerate an `AccountReader` that can't filter by magic, source magic numbers some
   other way, or accept this as a permanent limitation of this MCP server) before a real
   end-to-end run (real market data AND real account data together) is possible.

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
