"""
Real MarketDataSource backed by metatrader-mcp-server (extended locally with one tool -- see
scripts/metatrader_mcp_extended_server.py's module docstring for why). Only ever calls through
mcp_adapter.McpClient (never holds an MCP session directly), which itself enforces the
ToolRegistry gate -- get_bars/get_tick/get_symbol_info all resolve to READ_ONLY tools.

get_symbol_info() previously raised UnsupportedByServerError: none of metatrader-mcp-server's
own 25 tools expose point/digits/stops_level/freeze_level/volume_min/max/step for a symbol.
Resolved by adding a locally-registered get_symbol_info tool (see
scripts/metatrader_mcp_extended_server.py) that wraps metatrader_client.market.get_symbol_info
-- a method the third-party package already has, just never exposed via @mcp.tool(). The raw
MT5 field-name mapping used below is based on documented SymbolInfo fields, not yet confirmed
against a live capture from this project's own connection -- see
docs/MCP_ADAPTER_WIRING_CHECKPOINT.md for the pending live-verification step.
"""

from __future__ import annotations

import json

from mt5_mcp_trading.domain.models import MarketBar, SymbolInfo, Tick
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.mt5_adapter.metatrader_parsing import (
    parse_dataframe_csv,
    parse_iso_datetime,
    parse_symbol_filling_modes,
)


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
        raw = await self._client.call_tool("get_symbol_info", {"symbol_name": symbol})
        data = json.loads(raw)
        return SymbolInfo(
            symbol=data["name"],
            digits=int(data["digits"]),
            point=float(data["point"]),
            volume_min=float(data["volume_min"]),
            volume_max=float(data["volume_max"]),
            volume_step=float(data["volume_step"]),
            stops_level=int(data["trade_stops_level"]),
            freeze_level=int(data["trade_freeze_level"]),
            filling_modes=parse_symbol_filling_modes(int(data["filling_mode"])),
            spread=int(data["spread"]),
        )
