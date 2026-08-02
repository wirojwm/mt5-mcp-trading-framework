"""
Tests McpMarketDataSource against a stub McpClient rather than a live MCP session -- per
docs/MCP_ADAPTER_WIRING_CHECKPOINT.md, "Exact next smallest task" step 1. No live MCP call is
made anywhere in this file.

_StubMcpClient deliberately re-implements McpClient.call_tool's one safety-critical line
(`registry.authorize_call(name)` before returning anything) using the real, fully-classified
registry from metatrader_tools.build_metatrader_tool_registry(). This means a future change
that made McpMarketDataSource call an unclassified or TRADING-classified tool would make these
tests raise ToolNotClassifiedError/TradingDisabledError, the same way it would against a real
McpClient -- not just silently return canned data.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import pytest

from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import ToolClass, ToolRegistry
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource

# Same shape as the real Phase 3 capture used in test_mt5_adapter_parsing.py: newest-first,
# blank-named leading index column, explicit "+00:00" offset.
REAL_CANDLES_CSV = (
    ",time,open,high,low,close,tick_volume,spread,real_volume\n"
    "1,2026-08-01 15:43:00+00:00,63022.65,63022.73,63022.55,63022.62,71,1500,0\n"
    "0,2026-08-01 15:42:00+00:00,63022.96,63023.59,63019.57,63022.65,92,1500,0\n"
)

REAL_TICK_JSON = (
    '{"symbol": "BTCUSD", "bid": 63025.30, "ask": 63025.80, "time": "2026-08-01T15:46:35Z"}'
)

# Shape based on documented MetaTrader5 SymbolInfo fields (see
# scripts/metatrader_mcp_extended_server.py's module docstring) -- not yet confirmed against a
# live capture. Includes extra fields real MT5 responses carry that this project doesn't use,
# to prove they're ignored rather than required.
SYMBOL_INFO_JSON = (
    '{"custom": false, "chart_mode": 0, "select": true, "visible": true, "digits": 2, '
    '"spread": 150, "trade_stops_level": 0, "trade_freeze_level": 0, "filling_mode": 3, '
    '"point": 0.01, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01, '
    '"name": "BTCUSD", "description": "Bitcoin vs US Dollar", "path": "Crypto\\\\BTCUSD"}'
)


class _StubMcpClient:
    def __init__(
        self,
        responses: dict[str, str],
        registry: Optional[ToolRegistry] = None,
        fail: frozenset[str] = frozenset(),
    ) -> None:
        self._responses = responses
        self.registry = registry if registry is not None else build_metatrader_tool_registry()
        self._fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> str:
        self.registry.authorize_call(name)  # mirrors McpClient.call_tool's enforcement
        self.calls.append((name, arguments or {}))
        if name in self._fail:
            raise ConnectionError(f"stub: {name} failed")
        return self._responses[name]


# ---------- get_bars ----------

def test_get_bars_parses_and_sorts_oldest_to_newest() -> None:
    client = _StubMcpClient({"get_candles_latest": REAL_CANDLES_CSV})
    source = McpMarketDataSource(client)
    bars = asyncio.run(source.get_bars("BTCUSD", "M1", count=2))
    assert [b.close for b in bars] == [63022.65, 63022.62]  # 15:42 (older) before 15:43
    assert client.calls == [
        ("get_candles_latest", {"symbol_name": "BTCUSD", "timeframe": "M1", "count": 2})
    ]


def test_get_bars_raises_on_malformed_csv_missing_close_column() -> None:
    malformed = ",time,open,high,low,tick_volume,spread\n0,2026-08-01 15:42:00+00:00,1,2,0,1,0\n"
    client = _StubMcpClient({"get_candles_latest": malformed})
    source = McpMarketDataSource(client)
    with pytest.raises(KeyError):
        asyncio.run(source.get_bars("BTCUSD", "M1", count=1))


def test_get_bars_raises_on_non_numeric_price_field() -> None:
    malformed = (
        ",time,open,high,low,close,tick_volume,spread\n"
        "0,2026-08-01 15:42:00+00:00,not-a-number,2,0,1,1,0\n"
    )
    client = _StubMcpClient({"get_candles_latest": malformed})
    source = McpMarketDataSource(client)
    with pytest.raises(ValueError):
        asyncio.run(source.get_bars("BTCUSD", "M1", count=1))


# ---------- get_tick ----------

def test_get_tick_parses_zulu_timestamp() -> None:
    client = _StubMcpClient({"get_symbol_price": REAL_TICK_JSON})
    source = McpMarketDataSource(client)
    tick = asyncio.run(source.get_tick("BTCUSD"))
    assert tick.bid == 63025.30
    assert tick.ask == 63025.80
    assert tick.time.isoformat() == "2026-08-01T15:46:35+00:00"


def test_get_tick_raises_on_malformed_json() -> None:
    client = _StubMcpClient({"get_symbol_price": "{not json"})
    source = McpMarketDataSource(client)
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(source.get_tick("BTCUSD"))


def test_get_tick_raises_on_missing_field() -> None:
    client = _StubMcpClient({"get_symbol_price": '{"symbol": "BTCUSD", "bid": 1.0}'})  # no "ask"
    source = McpMarketDataSource(client)
    with pytest.raises(KeyError):
        asyncio.run(source.get_tick("BTCUSD"))


# ---------- get_symbol_info ----------

def test_get_symbol_info_parses_broker_specs() -> None:
    client = _StubMcpClient({"get_symbol_info": SYMBOL_INFO_JSON})
    source = McpMarketDataSource(client)
    info = asyncio.run(source.get_symbol_info("BTCUSD"))
    assert info.symbol == "BTCUSD"
    assert info.digits == 2
    assert info.point == 0.01
    assert (info.volume_min, info.volume_max, info.volume_step) == (0.01, 100.0, 0.01)
    assert (info.stops_level, info.freeze_level) == (0, 0)
    assert info.filling_modes == ("FOK", "IOC")  # bitmask 3 = FOK(1) | IOC(2)
    assert info.spread == 150
    assert client.calls == [("get_symbol_info", {"symbol_name": "BTCUSD"})]


def test_get_symbol_info_raises_on_malformed_json() -> None:
    client = _StubMcpClient({"get_symbol_info": "{not json"})
    source = McpMarketDataSource(client)
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(source.get_symbol_info("BTCUSD"))


def test_get_symbol_info_raises_on_missing_field() -> None:
    data = '{"name": "BTCUSD", "digits": 2}'  # no "point" and the rest
    client = _StubMcpClient({"get_symbol_info": data})
    source = McpMarketDataSource(client)
    with pytest.raises(KeyError):
        asyncio.run(source.get_symbol_info("BTCUSD"))


# ---------- ToolRegistry authorization enforcement ----------

def test_market_data_source_only_calls_read_only_classified_tools() -> None:
    client = _StubMcpClient(
        {
            "get_candles_latest": REAL_CANDLES_CSV,
            "get_symbol_price": REAL_TICK_JSON,
            "get_symbol_info": SYMBOL_INFO_JSON,
        }
    )
    source = McpMarketDataSource(client)
    asyncio.run(source.get_bars("BTCUSD", "M1", count=2))
    asyncio.run(source.get_tick("BTCUSD"))
    asyncio.run(source.get_symbol_info("BTCUSD"))
    assert len(client.calls) == 3
    for name, _ in client.calls:
        assert client.registry.classification_of(name) == ToolClass.READ_ONLY
