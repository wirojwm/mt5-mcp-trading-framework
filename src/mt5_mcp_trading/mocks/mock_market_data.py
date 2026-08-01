"""In-memory MarketDataSource for tests. No network, no MT5, no MCP — ever."""

from __future__ import annotations

from mt5_mcp_trading.domain.models import MarketBar, SymbolInfo, Tick


class MockMarketDataSource:
    def __init__(
        self,
        bars: dict[str, list[MarketBar]] | None = None,
        ticks: dict[str, Tick] | None = None,
        symbol_infos: dict[str, SymbolInfo] | None = None,
    ) -> None:
        self._bars = bars or {}
        self._ticks = ticks or {}
        self._symbol_infos = symbol_infos or {}

    async def get_bars(self, symbol: str, timeframe: str, count: int) -> list[MarketBar]:
        bars = self._bars.get(symbol, [])
        return bars[-count:] if count > 0 else []

    async def get_tick(self, symbol: str) -> Tick:
        if symbol not in self._ticks:
            raise KeyError(f"no mock tick configured for {symbol!r}")
        return self._ticks[symbol]

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        if symbol not in self._symbol_infos:
            raise KeyError(f"no mock symbol info configured for {symbol!r}")
        return self._symbol_infos[symbol]
