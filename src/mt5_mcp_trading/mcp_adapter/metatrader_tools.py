"""
Real tool classification for metatrader-mcp-server==0.5.1, confirmed via a live tools/list
enumeration in Phase 3 (see docs/mcp_tool_classification.md for the full writeup) -- not
guessed from the PyPI page, which is inaccurate (claims 32 tools; lists a get_symbol_info
tool that doesn't exist upstream; get_candles_by_date is documented there but isn't actually
registered -- confirmed absent from the live list too).

25 tools total from the upstream package: 13 read-only, 12 trading. Plus three tools this
project registers locally -- `get_symbol_info`, `get_positions_with_magic`,
`get_pending_orders_with_magic` -- see scripts/metatrader_mcp_extended_server.py's module
docstring for why each is added rather than upstream. All three are read-only by function
(get_symbol_info only calls metatrader_client.market.get_symbol_info; the other two only call
MetaTrader5.positions_get()/orders_get() -- no write/order-placement call of any kind), so all
three are classified READ_ONLY here alongside the 13 upstream ones: 28 tools total, 16
read-only, 12 trading.

If metatrader-mcp-server is ever upgraded, re-run the Phase 3 methodology (live tools/list +
read the installed version's server.py source) before trusting this list again -- do not
assume it still matches, and re-check that these three tools still aren't registered upstream
(if any is, scripts/metatrader_mcp_extended_server.py's corresponding local tool becomes
redundant and should be removed rather than shadow/duplicate the real one).
"""

from __future__ import annotations

from mt5_mcp_trading.mcp_adapter.tool_registry import ToolClass, ToolRegistry

READ_ONLY_TOOLS: tuple[str, ...] = (
    "get_account_info",
    "get_deals",
    "get_orders",
    "get_candles_latest",
    "get_symbol_price",
    "get_all_symbols",
    "get_symbols",
    "get_all_positions",
    "get_positions_by_symbol",
    "get_positions_by_id",
    "get_all_pending_orders",
    "get_pending_orders_by_symbol",
    "get_pending_orders_by_id",
    "get_symbol_info",  # locally registered -- see module docstring above
    "get_positions_with_magic",  # locally registered -- see module docstring above
    "get_pending_orders_with_magic",  # locally registered -- see module docstring above
)

TRADING_TOOLS: tuple[str, ...] = (
    "place_market_order",
    "place_pending_order",
    "modify_position",
    "modify_pending_order",
    "close_position",
    "cancel_pending_order",
    "close_all_positions",
    "close_all_positions_by_symbol",
    "close_all_profitable_positions",
    "close_all_losing_positions",
    "cancel_all_pending_orders",
    "cancel_pending_orders_by_symbol",
)


def build_metatrader_tool_registry(trading_enabled: bool = False) -> ToolRegistry:
    """trading_enabled defaults to False -- callers must opt in explicitly, matching
    "MCP trading tools must remain disabled or unused until explicit approval"."""
    registry = ToolRegistry(trading_enabled=trading_enabled)
    for name in READ_ONLY_TOOLS:
        registry.classify(name, ToolClass.READ_ONLY)
    for name in TRADING_TOOLS:
        registry.classify(name, ToolClass.TRADING)
    return registry
