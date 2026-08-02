# metatrader-mcp-server tool classification

Derived from a **live `tools/list` call** against the connected demo account (25 tools
returned), cross-checked against the installed package's source (`server.py`, `mcp==1.9.4`
pinned build of `metatrader-mcp-server==0.5.1`). Not from the PyPI page, which is inaccurate
(claims 32 tools; also lists a `get_symbol_info` tool that does not exist upstream).

`get_candles_by_date` is defined in the package's source but is missing its `@mcp.tool()`
decorator, so it is **not** actually registered — confirmed absent from the live list. It is
a bug in the third-party package, not something on our side.

**This project's own server process exposes 26 tools, not 25**: `scripts/run_metatrader_mcp_stdio.py`
launches `scripts/metatrader_mcp_extended_server.py`, which re-registers the same 25 upstream
tools plus one this project adds locally, `get_symbol_info` — see that script's module
docstring and Known Issues item 6 below for why. Everything else in this document describes
the upstream `metatrader-mcp-server` package as installed; the 26th tool is called out
explicitly wherever it applies.

## Terminal and account

| Tool | Notes |
|---|---|
| `get_account_info` | Balance/equity/margin/leverage/currency, plus a buggy `account_type` field — see Known Issues below. **Do not trust `account_type` as a demo/live gate.** |

No tool exposes terminal connection state, build number, or `trade_allowed` — there is no
`get_terminal_info` equivalent on this server. Confirmed via a direct, read-only
`MetaTrader5.terminal_info()` call outside MCP when that information was needed.

## Symbols and market data (read-only)

| Tool | Notes |
|---|---|
| `get_all_symbols` | All available symbols |
| `get_symbols` | Filtered by group pattern (e.g. `*USD*`) |
| `get_symbol_price` | Latest bid/ask/last for one symbol |
| `get_candles_latest` | Latest N candles for symbol+timeframe |
| ~~`get_candles_by_date`~~ | **Not actually callable** — defined in source, missing `@mcp.tool()`, absent from the live list |
| `get_symbol_info` *(locally added, not upstream)* | Broker specifications for a symbol (point/digits/volume_min/max/step/trade_stops_level/trade_freeze_level/filling_mode/spread). Registered by `scripts/metatrader_mcp_extended_server.py`, not by `metatrader-mcp-server` itself — see Known Issues item 6. |

## Positions and orders (read-only)

| Tool | Notes |
|---|---|
| `get_all_positions` | All open positions |
| `get_positions_by_symbol` | Open positions for one symbol |
| `get_positions_by_id` | Open position by ticket id |
| `get_all_pending_orders` | All pending (not yet filled) orders |
| `get_pending_orders_by_symbol` | Pending orders for one symbol |
| `get_pending_orders_by_id` | Pending order by ticket id |
| `get_deals` | Historical deals (CSV), read-only |
| `get_orders` | Historical orders (CSV), read-only |

None of the position/order tools above expose a magic number (or comment) field — see Known
Issues item 5.

## Workspace and files

None. This server has no filesystem/workspace tools at all.

## MetaEditor and compile

None. This server has no MQL5/MetaEditor/compilation tools at all.

## Trading and execution (write — never called by this project without explicit approval)

| Tool | Effect |
|---|---|
| `place_market_order` | Opens a real position |
| `place_pending_order` | Places a pending order |
| `modify_position` | Changes SL/TP on an open position |
| `modify_pending_order` | Changes price/SL/TP on a pending order |
| `close_position` | Closes one open position |
| `cancel_pending_order` | Cancels one pending order |
| `close_all_positions` | Closes every open position |
| `close_all_positions_by_symbol` | Closes every open position for one symbol |
| `close_all_profitable_positions` | Closes every position currently in profit |
| `close_all_losing_positions` | Closes every position currently at a loss |
| `cancel_all_pending_orders` | Cancels every pending order |
| `cancel_pending_orders_by_symbol` | Cancels every pending order for one symbol |

There is no server-side permission tier separating these from the read-only tools above —
once connected, all 26 (25 upstream, well 24 real + 1 phantom, plus this project's own locally
added `get_symbol_info`) are equally callable. Our own `ToolRegistry`
(`src/mt5_mcp_trading/mcp_adapter/tool_registry.py`) is what enforces the separation for this
project's own code, and none of these 12 have ever been called by this project.

## Known issues in the third-party package (found during Phase 3, not fixed by us)

1. **`get_account_type()` maps `trade_mode` to a string backwards.** MT5's real, documented
   enum is `DEMO=0, CONTEST=1, REAL=2`. The package's mapping is `0->"real", 1->"demo",
   2->"contest"` — shifted by one position. Verified independently: the connected account's
   raw `trade_mode` (read directly via `MetaTrader5.account_info()`, bypassing this server
   entirely) is `0`, i.e. genuinely **DEMO**, while `get_account_info`'s `account_type` field
   reports `"real"`. **Any future code must not use this field to gate execution mode** —
   a real account would report `"contest"` and a demo account reports `"real"`, which is
   exactly backwards from what a naive safety check would assume.
2. **Terminal path auto-detection** only checks a fixed list of "standard" MetaQuotes
   install paths; white-labeled broker terminals aren't in that list and the resulting
   `initialize(path=None)` call fails outright rather than falling back gracefully. Worked
   around via an explicit `MT5_PATH` env var (see `scripts/run_metatrader_mcp_stdio.py`).
3. **Indefinite retry on init failure** rather than a bounded number of attempts with a clean
   error — a credential or path problem looks like a hang, not a fast failure.
4. **`get_candles_by_date` is unreachable** despite being documented — see above.
5. **No magic number (or comment) on positions/orders.** Confirmed via source
   (`metatrader_client.utils.convert_positions_to_dataframe` / `convert_orders_to_dataframe`):
   both use a hardcoded column mapping that omits `magic` and `comment` entirely, even though
   MT5's raw position/order data includes them. This project's `McpAccountReader` (in
   `mt5_adapter/mcp_account.py`) raises `MagicFilteringUnavailableError` if a caller asks to
   filter `get_positions()`/`get_orders()` by `magic` — returning unfiltered data silently
   mislabeled as filtered would be worse than refusing, given how central magic-based
   segregation is to this project's risk model (grid vs. runner, duplicate-order prevention,
   exposure caps). When `magic` is not requested, every returned `PositionState`/`OrderState`
   carries the sentinel `magic=UNKNOWN_MAGIC` (0), not a guess.
6. **No `SymbolInfo`-equivalent tool exists upstream.** None of `metatrader-mcp-server`'s own
   25 tools expose a symbol's `point`/`digits`/`stops_level`/`freeze_level`/`volume_min`/
   `volume_max`/`volume_step` (the PyPI page's claimed `get_symbol_info` tool does not actually
   exist — see the top of this document). **Resolved locally**, not upstream: the package's
   own dependency, `metatrader_client`, already implements this correctly
   (`metatrader_client.market.get_symbol_info()`, confirmed by reading its source directly —
   it wraps `MetaTrader5.symbols_get(symbol_name)` and returns the full raw `SymbolInfo` dict);
   `metatrader_mcp/server.py` just never registers it with `@mcp.tool()`, the same class of bug
   as `get_candles_by_date` above. `scripts/metatrader_mcp_extended_server.py` adds exactly
   this one tool locally by importing the upstream package's already-built `FastMCP` instance
   and registering one more tool alongside its 25 — not a fork, no upstream code is modified.
   `McpMarketDataSource.get_symbol_info()` (in `mt5_adapter/mcp_market_data.py`) now calls it
   and parses the response into this project's `SymbolInfo` domain model. The raw-field-name
   mapping used (`name`, `digits`, `point`, `volume_min`, `volume_max`, `volume_step`,
   `trade_stops_level`, `trade_freeze_level`, `filling_mode`, `spread`) is based on documented
   MetaTrader5 `SymbolInfo` fields, **not yet confirmed against a live capture** — see
   `docs/MCP_ADAPTER_WIRING_CHECKPOINT.md` for the pending live-verification step that will
   confirm or correct it, following this project's established practice of correcting
   documentation-based parsing against real captured output before trusting it fully.
