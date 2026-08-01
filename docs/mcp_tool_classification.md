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
