#!/usr/bin/env python3
"""
Local extension of the third-party `metatrader-mcp-server` (v0.5.1) that adds exactly one
read-only tool it's missing: `get_symbol_info`.

Why this exists: `metatrader_client.market.get_symbol_info()` -- the library
metatrader-mcp-server itself depends on and already has installed -- already implements this
correctly. It wraps `MetaTrader5.symbols_get(symbol_name)` and returns the full raw MT5
`SymbolInfo` struct as a dict (point/digits/volume_min/max/step/trade_stops_level/
trade_freeze_level/filling_mode/spread/...). `metatrader_mcp/server.py` simply never registers
it with `@mcp.tool()` -- confirmed by reading that file directly: the same class of bug as its
`get_candles_by_date` (defined in source, missing the decorator, absent from the live
`tools/list`; see docs/mcp_tool_classification.md). Not guessed -- read from the installed
package's source (`.venv/Lib/site-packages/metatrader_client/market/get_symbol_info.py` and
`.venv/Lib/site-packages/metatrader_mcp/server.py`).

This is NOT a fork. It imports `metatrader_mcp.server`'s already-built `mcp` FastMCP instance
and its `get_client`/`Context` helpers unmodified (importing that module has no side effects
outside `if __name__ == "__main__":`), adds one tool alongside the 25 the package already
registers, and launches it exactly the way that module's own `__main__` block does -- same
argv interface, same env-var credential injection -- so scripts/run_metatrader_mcp_stdio.py
needed only a one-line change (which script it launches). Credential handling is completely
unchanged.

The new tool's classification (READ_ONLY, in mcp_adapter/metatrader_tools.py) and everything
that calls it (mt5_adapter/mcp_market_data.py's `get_symbol_info()`) still go through this
project's own `McpClient`/`ToolRegistry` gate exactly like the other 25 tools -- this script
does not bypass any of this project's own safety layers, it only adds one capability to the
server process those layers talk to.

Raw MT5 field-name mapping used here (`name`, `digits`, `point`, `volume_min`, `volume_max`,
`volume_step`, `trade_stops_level`, `trade_freeze_level`, `filling_mode`, `spread`) is based on
MetaTrader5 Python API's documented `SymbolInfo` fields, matching MQL5's `SYMBOL_*` constants
-- not yet confirmed against a live capture from this project's own connection. See
docs/MCP_ADAPTER_WIRING_CHECKPOINT.md for the pending live-verification step that will confirm
or correct this.
"""

# Deliberately NOT `from __future__ import annotations` (unlike the rest of this project):
# FastMCP's Tool.from_function() inspects @mcp.tool()-decorated functions' raw parameter
# annotations at import time via issubclass(param.annotation, Context) -- with postponed
# evaluation, param.annotation is the string "Context" instead of the class, and that call
# raises TypeError. metatrader_mcp/server.py's own tools don't use postponed annotations
# either, for the same reason. Confirmed by hitting this exact TypeError while wiring this up.
import argparse
import os

from dotenv import load_dotenv

from metatrader_mcp.server import Context, get_client, mcp


@mcp.tool()
def get_symbol_info(ctx: Context, symbol_name: str) -> dict:
    """Get broker specifications for a symbol: point, digits, volume_min/max/step,
    trade_stops_level, trade_freeze_level, filling_mode (bitmask), spread, and other raw MT5
    SymbolInfo fields, as a dict."""
    client = get_client(ctx)
    return client.market.get_symbol_info(symbol_name=symbol_name)


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

    if args.login:
        os.environ["login"] = args.login
    if args.password:
        os.environ["password"] = args.password
    if args.server:
        os.environ["server"] = args.server
    if args.path:
        os.environ["MT5_PATH"] = args.path

    transport, host, port = resolve_transport_config(args.transport, args.host, args.port)
    run_mcp(mcp, transport, host, port)
