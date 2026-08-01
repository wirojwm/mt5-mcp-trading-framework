"""
Real MarketDataSource backed by metatrader-mcp-server. Only ever calls through
mcp_adapter.McpClient (never holds an MCP session directly), which itself enforces the
ToolRegistry gate -- get_bars/get_tick/get_symbol_info all resolve to READ_ONLY tools (or, for
get_symbol_info, no tool at all -- see below).

get_symbol_info() cannot be implemented: confirmed in Phase 3 that none of
metatrader-mcp-server's 25 tools expose point/digits/stops_level/freeze_level/
volume_min/max/step for a symbol. It raises UnsupportedByServerError rather than fabricating
placeholder values -- order_planning.build_order_plan() needs real SymbolInfo to normalize
prices and clamp volume correctly; a fabricated SymbolInfo would silently produce wrong
OrderPlans (e.g. an "Invalid price" rejection on the real broker, or worse, an accepted order
at a materially wrong price) instead of a loud, obvious failure. A caller needing SymbolInfo
today must supply it from elsewhere (e.g. a hardcoded per-symbol config) until this gap is
resolved -- by a different MCP server, a supplementary read-only tool, or some other source.
"""

from __future__ import annotations

import json

from mt5_mcp_trading.domain.models import MarketBar, SymbolInfo, Tick
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.mt5_adapter.metatrader_parsing import parse_dataframe_csv, parse_iso_datetime


class UnsupportedByServerError(NotImplementedError):
    """Raised when the connected MCP server has no tool capable of answering the request."""


class McpMarketDataSource:
    def __init__(self, client: McpClient) -> None:
        self._client = client

    async def get_bars(self, symbol: str, timeframe: str, count: int) -> list[MarketBar]:
        raw = await self._client.call_tool(
            "get_candles_latest", {"symbol_name": symbol, "timeframe": timeframe, "count": count}
        )
        rows = parse_dataframe_csv(raw)
        bars = [
            MarketBar(
                symbol=symbol, timeframe=timeframe, time=parse_iso_datetime(row["time"]),
                open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                close=float(row["close"]), tick_volume=int(float(row["tick_volume"])),
                spread=int(float(row["spread"])),
            )
            for row in rows
        ]
        # metatrader-mcp-server returns candles newest-first; this project's convention
        # throughout (strategy/grid.py, strategy/runner.py, strategy/guard.py) is oldest-to-
        # newest with the most recent bar last -- sort explicitly rather than assume order.
        bars.sort(key=lambda b: b.time)
        return bars

    async def get_tick(self, symbol: str) -> Tick:
        raw = await self._client.call_tool("get_symbol_price", {"symbol_name": symbol})
        data = json.loads(raw)
        return Tick(
            symbol=symbol, bid=float(data["bid"]), ask=float(data["ask"]),
            time=parse_iso_datetime(data["time"]),
        )

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        raise UnsupportedByServerError(
            f"metatrader-mcp-server exposes no tool for {symbol}'s broker specifications "
            f"(point/digits/stops_level/freeze_level/volume_min/max/step) -- confirmed "
            f"absent from its full 25-tool surface. See this module's docstring."
        )
