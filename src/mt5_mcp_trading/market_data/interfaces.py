"""
Read-only market data access. Async because real implementations talk to an MCP server over
IPC; this Protocol is implementation-agnostic so mock/mcp-backed/recorded implementations can
all satisfy it identically.

This interface has no order-placement capability at all — that is `mt5_adapter.OrderExecutor`,
a deliberately separate Protocol.
"""

from __future__ import annotations

from typing import Protocol

from mt5_mcp_trading.domain.models import MarketBar, SymbolInfo, Tick


class MarketDataSource(Protocol):
    async def get_bars(self, symbol: str, timeframe: str, count: int) -> list[MarketBar]:
        ...

    async def get_tick(self, symbol: str) -> Tick:
        ...

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        ...
