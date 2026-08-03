#!/usr/bin/env python3
"""
Local extension of the third-party `metatrader-mcp-server` (v0.5.1) that adds three read-only
tools it's missing: `get_symbol_info`, `get_positions_with_magic`, `get_pending_orders_with_magic`.

## get_symbol_info

`metatrader_client.market.get_symbol_info()` -- the library metatrader-mcp-server itself
depends on and already has installed -- already implements this correctly. It wraps
`MetaTrader5.symbols_get(symbol_name)` and returns the full raw MT5 `SymbolInfo` struct as a
dict (point/digits/volume_min/max/step/trade_stops_level/trade_freeze_level/filling_mode/
spread/...). `metatrader_mcp/server.py` simply never registers it with `@mcp.tool()` --
confirmed by reading that file directly: the same class of bug as its `get_candles_by_date`
(defined in source, missing the decorator, absent from the live `tools/list`; see
docs/mcp_tool_classification.md). Not guessed -- read from the installed package's source
(`.venv/Lib/site-packages/metatrader_client/market/get_symbol_info.py` and
`.venv/Lib/site-packages/metatrader_mcp/server.py`).

## get_positions_with_magic / get_pending_orders_with_magic

Unlike `get_symbol_info`, there is no already-complete `metatrader_client` method to just
expose: `metatrader_client.utils.convert_positions_to_dataframe`/`convert_orders_to_dataframe`
genuinely support a `columns_mapping` parameter that could include `magic`/`comment`, but every
call site above them (`metatrader_client/order/get_positions.py`,
`metatrader_client/order/get_pending_orders.py`, and everything `metatrader_mcp/server.py`
calls) hardcodes it to `None` with no way to override -- confirmed by reading those files
directly, not guessed. This is why `metatrader-mcp-server`'s own `get_all_positions`/
`get_positions_by_symbol`/`get_all_pending_orders`/`get_pending_orders_by_symbol` never expose
`magic`, and why `McpAccountReader` used to raise `MagicFilteringUnavailableError` whenever a
caller asked to filter by magic (see `mt5_adapter/mcp_account.py`'s docstring history).

The raw MT5 `TradePosition`/`TradeOrder` structs *do* carry `.magic` and `.comment` (confirmed
via safe, read-only introspection of `MetaTrader5.TradePosition`/`TradeOrder`'s fields, no live
connection needed for that check). So these two new tools call `MetaTrader5.positions_get()`/
`orders_get()` directly -- mirroring the ~6-line dispatch `metatrader_client/order/
get_positions.py`/`get_pending_orders.py` already use internally (real MT5 connections are
process-global, not object-scoped, so this is not a new or riskier pattern, it's the exact same
one those files already use) -- then feed the result through `metatrader_client.utils`'s own
`convert_positions_to_dataframe`/`convert_orders_to_dataframe` with an explicit
`columns_mapping` that adds `magic`/`comment` to the existing default mapping. This reuses all
of `metatrader_client`'s real value (formatting, sorting, enum-decoding) and duplicates only
the trivial fetch step nothing else exposes. Ticket-based lookup and the optional symbol/group
pre-validation `metatrader_client` does before fetching are both skipped here: `McpAccountReader`
only ever needs "all" or "by symbol", and an invalid symbol/group passed straight to
`positions_get()`/`orders_get()` already just returns an empty result on its own.

## Shared

This is NOT a fork. It imports `metatrader_mcp.server`'s already-built `mcp` FastMCP instance
and its `get_client`/`Context` helpers unmodified (importing that module has no side effects
outside `if __name__ == "__main__":`), adds tools alongside the 25 the package already
registers, and launches it exactly the way that module's own `__main__` block does -- same
argv interface, same env-var credential injection -- so scripts/run_metatrader_mcp_stdio.py
needed only a one-line change (which script it launches). Credential handling is completely
unchanged.

Every new tool's classification (READ_ONLY, in mcp_adapter/metatrader_tools.py) and everything
that calls them (mt5_adapter/mcp_market_data.py's `get_symbol_info()`,
mt5_adapter/mcp_account.py's `get_positions()`/`get_orders()`) still go through this project's
own `McpClient`/`ToolRegistry` gate exactly like the other 25 tools -- this script does not
bypass any of this project's own safety layers, it only adds capabilities to the server process
those layers talk to.

Raw MT5 field-name mapping used for `get_symbol_info` (`name`, `digits`, `point`, `volume_min`,
`volume_max`, `volume_step`, `trade_stops_level`, `trade_freeze_level`, `filling_mode`,
`spread`) is based on MetaTrader5 Python API's documented `SymbolInfo` fields, matching MQL5's
`SYMBOL_*` constants -- **live-confirmed correct**, see docs/MCP_ADAPTER_WIRING_CHECKPOINT.md.
"""

# Deliberately NOT `from __future__ import annotations` (unlike the rest of this project):
# FastMCP's Tool.from_function() inspects @mcp.tool()-decorated functions' raw parameter
# annotations at import time via issubclass(param.annotation, Context) -- with postponed
# evaluation, param.annotation is the string "Context" instead of the class, and that call
# raises TypeError. metatrader_mcp/server.py's own tools don't use postponed annotations
# either, for the same reason. Confirmed by hitting this exact TypeError while wiring this up.
import argparse
import os
from typing import Optional

import MetaTrader5 as mt5
from dotenv import load_dotenv
from metatrader_client.utils import convert_orders_to_dataframe, convert_positions_to_dataframe

from metatrader_mcp.server import Context, get_client, mcp

# metatrader_client.utils.convert_positions_to_dataframe's own default columns_mapping (see
# that function's source) plus magic/comment, which it omits.
_POSITIONS_COLUMNS_MAPPING = {
    "ticket": "id",
    "time": "time",
    "symbol": "symbol",
    "type": "type",
    "volume": "volume",
    "price_open": "open",
    "sl": "stop_loss",
    "tp": "take_profit",
    "profit": "profit",
    "magic": "magic",
    "comment": "comment",
}

# convert_orders_to_dataframe's own default columns_mapping plus magic/comment.
_ORDERS_COLUMNS_MAPPING = {
    "ticket": "id",
    "time_setup": "time",
    "symbol": "symbol",
    "type": "type",
    "volume_current": "volume",
    "price_open": "open",
    "sl": "stop_loss",
    "tp": "take_profit",
    "state": "state",
    "type_time": "type_time",
    "type_filling": "type_filling",
    "time_expiration": "expiration",
    "magic": "magic",
    "comment": "comment",
}


@mcp.tool()
def get_symbol_info(ctx: Context, symbol_name: str) -> dict:
    """Get broker specifications for a symbol: point, digits, volume_min/max/step,
    trade_stops_level, trade_freeze_level, filling_mode (bitmask), spread, and other raw MT5
    SymbolInfo fields, as a dict."""
    client = get_client(ctx)
    return client.market.get_symbol_info(symbol_name=symbol_name)


@mcp.tool()
def get_positions_with_magic(ctx: Context, symbol_name: Optional[str] = None) -> str:
    """Get open positions, including their magic number and comment -- unlike
    get_all_positions/get_positions_by_symbol, which drop both. CSV, same shape as those two
    tools plus magic/comment columns. Omit symbol_name for all positions."""
    # Doesn't need get_client(ctx)/metatrader_client's order module at all: MetaTrader5.
    # positions_get() operates on the process-global terminal connection this server's
    # lifespan already established (see app_lifespan in metatrader_mcp/server.py) -- the same
    # way metatrader_client/order/get_positions.py calls it internally, ctx is only required
    # because @mcp.tool() functions must accept it.
    positions = mt5.positions_get(symbol=symbol_name) if symbol_name else mt5.positions_get()
    df = convert_positions_to_dataframe(positions, columns_mapping=_POSITIONS_COLUMNS_MAPPING)
    return df.to_csv()


@mcp.tool()
def get_pending_orders_with_magic(ctx: Context, symbol_name: Optional[str] = None) -> str:
    """Get pending orders, including their magic number and comment -- unlike
    get_all_pending_orders/get_pending_orders_by_symbol, which drop both. CSV, same shape as
    those two tools plus magic/comment columns. Omit symbol_name for all pending orders."""
    orders = mt5.orders_get(symbol=symbol_name) if symbol_name else mt5.orders_get()
    df = convert_orders_to_dataframe(orders, columns_mapping=_ORDERS_COLUMNS_MAPPING)
    return df.to_csv()


if __name__ == "__main__":
    load_dotenv()
    from metatrader_mcp.utils import resolve_transport_config, run_mcp

    # Mirrors metatrader_mcp/server.py's own __main__ block's argv interface exactly, so
    # scripts/run_metatrader_mcp_stdio.py's invocation of this script needs no other changes.
    parser = argparse.ArgumentParser(description="MetaTrader MCP Server (+ get_symbol_info)")
    parser.add_argument("--login", type=str, help="MT5 login")
    parser.add_argument("--password", type=str, help="MT5 password")
    parser.add_argument("--server", type=str, help="MT5 server name")
    parser.add_argument("--path", type=str, help="Path to MT5 terminal executable (optional)")
    parser.add_argument(
        "--transport", type=str, choices=["sse", "stdio", "streamable-http"], default=None
    )
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    # login/password/server fall back to MT5_DEMO_* env vars (already inherited from the
    # parent process -- scripts/run_metatrader_mcp_stdio.py loads them from .env into its own
    # environment before launching this script, and subprocess.run() inherits the parent's
    # environment by default) when not passed via argv. Prevents credentials from ever
    # appearing in this process's command line, which -- unlike its environment -- is visible
    # to any process/user on the machine via ordinary, unprivileged process listing (confirmed
    # live: found the plaintext password this way while diagnosing an unrelated issue, see
    # docs/PIPELINE_WIRING_CHECKPOINT.md). run_metatrader_mcp_stdio.py no longer puts them on
    # argv at all; --login/--password/--server here remain accepted (not removed) only so this
    # script's own argv interface still mirrors metatrader_mcp/server.py's, per the comment
    # above -- not because anything should still pass secrets through them.
    login = args.login or os.environ.get("MT5_DEMO_LOGIN")
    password = args.password or os.environ.get("MT5_DEMO_PASSWORD")
    server = args.server or os.environ.get("MT5_DEMO_SERVER")

    if login:
        os.environ["login"] = login
    if password:
        os.environ["password"] = password
    if server:
        os.environ["server"] = server
    if args.path:
        os.environ["MT5_PATH"] = args.path

    transport, host, port = resolve_transport_config(args.transport, args.host, args.port)
    run_mcp(mcp, transport, host, port)
