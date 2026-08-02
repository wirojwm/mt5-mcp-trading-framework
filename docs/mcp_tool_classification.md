# metatrader-mcp-server tool classification

Derived from a **live `tools/list` call** against the connected demo account (25 tools
returned), cross-checked against the installed package's source (`server.py`, `mcp==1.9.4`
pinned build of `metatrader-mcp-server==0.5.1`). Not from the PyPI page, which is inaccurate
(claims 32 tools; also lists a `get_symbol_info` tool that does not exist).

`get_candles_by_date` is defined in the package's source but is missing its `@mcp.tool()`
decorator, so it is **not** actually registered — confirmed absent from the live list. It is
a bug in the third-party package, not something on our side.

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
Issues item 5. None of the tools in this file expose per-symbol broker specifications
(point/digits/stops_level/freeze_level/volume_min/max/step) — see Known Issues item 6.

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
once connected, all 25 (well, 24 real + 1 phantom) are equally callable. Our own
`ToolRegistry` (`src/mt5_mcp_trading/mcp_adapter/tool_registry.py`) is what enforces the
separation for this project's own code, and none of these 12 have ever been called by this
project.

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
6. **No `SymbolInfo`-equivalent tool exists at all.** None of this server's 25 tools expose a
   symbol's `point`/`digits`/`stops_level`/`freeze_level`/`volume_min`/`volume_max`/
   `volume_step` (the PyPI page's claimed `get_symbol_info` tool does not actually exist — see
   the top of this document). This project's `McpMarketDataSource.get_symbol_info()` (in
   `mt5_adapter/mcp_market_data.py`) raises `UnsupportedByServerError` rather than fabricating
   placeholder values, since `order_planning.build_order_plan()` needs real `SymbolInfo` to
   normalize prices and clamp volume correctly — a fabricated value would risk an accepted
   order at a materially wrong price instead of a loud, obvious failure. Unresolved: a caller
   needing `SymbolInfo` today must supply it from elsewhere (e.g. a hardcoded per-symbol
   config) until this gap is resolved by a different MCP server, a supplementary read-only
   tool, or some other source.
