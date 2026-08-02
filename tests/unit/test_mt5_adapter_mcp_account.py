"""
Tests McpAccountReader against a stub McpClient rather than a live MCP session -- per
docs/MCP_ADAPTER_WIRING_CHECKPOINT.md, "Exact next smallest task" step 1. No live MCP call is
made anywhere in this file.

_StubMcpClient deliberately re-implements McpClient.call_tool's one safety-critical line
(`registry.authorize_call(name)` before returning anything) using the real, fully-classified
registry from metatrader_tools.build_metatrader_tool_registry() -- see
test_mt5_adapter_mcp_market_data.py's module docstring for why.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import pytest

from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import ToolClass, ToolRegistry
from mt5_mcp_trading.mt5_adapter.mcp_account import (
    UNKNOWN_MAGIC,
    MagicFilteringUnavailableError,
    McpAccountReader,
)

REAL_POSITIONS_CSV = (
    ",id,time,symbol,type,volume,open,stop_loss,take_profit,profit\n"
    "0,123456,2026-08-01 15:40:00+00:00,BTCUSD,BUY,0.10,63000.0,0.0,0.0,12.5\n"
)

REAL_ORDERS_CSV = (
    ",id,time,symbol,type,volume,open,stop_loss,take_profit,state,type_time,expiration\n"
    "0,654321,2026-08-01 15:41:00+00:00,BTCUSD,SELL_LIMIT,0.05,63500.0,0.0,0.0,STARTED,GTC,\n"
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


def _account_json(**overrides: Any) -> str:
    data = {"balance": 10000.0, "equity": 10050.0, "free_margin": 9800.0, "account_type": "real"}
    data.update(overrides)
    return json.dumps(data)


# ---------- get_account_state ----------

def test_get_account_state_parses_balance_equity_margin() -> None:
    client = _StubMcpClient({"get_account_info": _account_json()})
    reader = McpAccountReader(client)
    state = asyncio.run(reader.get_account_state())
    assert (state.balance, state.equity, state.margin_free) == (10000.0, 10050.0, 9800.0)
    assert state.login is None
    assert state.server is None


@pytest.mark.parametrize(
    "reported,expected",
    [("demo", "DEMO"), ("DEMO", "DEMO"), ("contest", "CONTEST"), ("real", "REAL")],
)
def test_get_account_state_uppercases_known_trade_modes(reported: str, expected: str) -> None:
    client = _StubMcpClient({"get_account_info": _account_json(account_type=reported)})
    reader = McpAccountReader(client)
    state = asyncio.run(reader.get_account_state())
    assert state.trade_mode == expected


@pytest.mark.parametrize("reported", ["", "unknown", "not_a_mode"])
def test_get_account_state_falls_back_to_real_for_unrecognized_trade_mode(reported: str) -> None:
    # Fail-safe per mcp_account.py's module docstring: an unrecognized account_type must never
    # be trusted as DEMO.
    client = _StubMcpClient({"get_account_info": _account_json(account_type=reported)})
    reader = McpAccountReader(client)
    state = asyncio.run(reader.get_account_state())
    assert state.trade_mode == "REAL"


def test_get_account_state_falls_back_to_real_when_account_type_missing() -> None:
    data = {"balance": 1.0, "equity": 1.0, "free_margin": 1.0}  # no "account_type" key at all
    client = _StubMcpClient({"get_account_info": json.dumps(data)})
    reader = McpAccountReader(client)
    state = asyncio.run(reader.get_account_state())
    assert state.trade_mode == "REAL"


def test_get_account_state_raises_on_malformed_json() -> None:
    client = _StubMcpClient({"get_account_info": "{not json"})
    reader = McpAccountReader(client)
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(reader.get_account_state())


def test_get_account_state_raises_on_missing_balance_field() -> None:
    data = {"equity": 1.0, "free_margin": 1.0}  # no "balance"
    client = _StubMcpClient({"get_account_info": json.dumps(data)})
    reader = McpAccountReader(client)
    with pytest.raises(KeyError):
        asyncio.run(reader.get_account_state())


# ---------- get_positions ----------

def test_get_positions_all_parses_row_and_sets_unknown_magic_sentinel() -> None:
    client = _StubMcpClient({"get_all_positions": REAL_POSITIONS_CSV})
    reader = McpAccountReader(client)
    positions = asyncio.run(reader.get_positions())
    assert len(positions) == 1
    p = positions[0]
    assert (p.ticket, p.symbol, p.side, p.volume, p.price_open, p.profit) == (
        123456, "BTCUSD", "BUY", 0.10, 63000.0, 12.5,
    )
    assert p.magic == UNKNOWN_MAGIC
    assert client.calls == [("get_all_positions", {})]


def test_get_positions_by_symbol_calls_the_symbol_scoped_tool() -> None:
    client = _StubMcpClient({"get_positions_by_symbol": REAL_POSITIONS_CSV})
    reader = McpAccountReader(client)
    asyncio.run(reader.get_positions(symbol="BTCUSD"))
    assert client.calls == [("get_positions_by_symbol", {"symbol": "BTCUSD"})]


def test_get_positions_with_magic_raises_before_any_mcp_call() -> None:
    client = _StubMcpClient({"get_all_positions": REAL_POSITIONS_CSV})
    reader = McpAccountReader(client)
    with pytest.raises(MagicFilteringUnavailableError):
        asyncio.run(reader.get_positions(magic=71101))
    assert client.calls == []  # confirms this never reaches the MCP client at all


def test_get_positions_raises_on_malformed_csv_missing_open_column() -> None:
    malformed = ",id,time,symbol,type,volume,stop_loss,take_profit,profit\n0,1,t,BTCUSD,BUY,0.1,0,0,1\n"
    client = _StubMcpClient({"get_all_positions": malformed})
    reader = McpAccountReader(client)
    with pytest.raises(KeyError):
        asyncio.run(reader.get_positions())


def test_get_positions_raises_on_non_numeric_volume() -> None:
    malformed = (
        ",id,time,symbol,type,volume,open,stop_loss,take_profit,profit\n"
        "0,1,t,BTCUSD,BUY,not-a-number,63000.0,0,0,1\n"
    )
    client = _StubMcpClient({"get_all_positions": malformed})
    reader = McpAccountReader(client)
    with pytest.raises(ValueError):
        asyncio.run(reader.get_positions())


# ---------- get_orders ----------

def test_get_orders_all_parses_row_and_sets_unknown_magic_sentinel() -> None:
    client = _StubMcpClient({"get_all_pending_orders": REAL_ORDERS_CSV})
    reader = McpAccountReader(client)
    orders = asyncio.run(reader.get_orders())
    assert len(orders) == 1
    o = orders[0]
    # SELL_LIMIT: _side_from_type only special-cases a "BUY" prefix, everything else is SELL.
    assert (o.ticket, o.symbol, o.side, o.volume, o.price) == (654321, "BTCUSD", "SELL", 0.05, 63500.0)
    assert o.magic == UNKNOWN_MAGIC
    assert client.calls == [("get_all_pending_orders", {})]


def test_get_orders_by_symbol_calls_the_symbol_scoped_tool() -> None:
    client = _StubMcpClient({"get_pending_orders_by_symbol": REAL_ORDERS_CSV})
    reader = McpAccountReader(client)
    asyncio.run(reader.get_orders(symbol="BTCUSD"))
    assert client.calls == [("get_pending_orders_by_symbol", {"symbol": "BTCUSD"})]


def test_get_orders_with_magic_raises_before_any_mcp_call() -> None:
    client = _StubMcpClient({"get_all_pending_orders": REAL_ORDERS_CSV})
    reader = McpAccountReader(client)
    with pytest.raises(MagicFilteringUnavailableError):
        asyncio.run(reader.get_orders(magic=71101))
    assert client.calls == []


# ---------- get_connection_state ----------

def test_get_connection_state_true_when_get_account_info_succeeds() -> None:
    client = _StubMcpClient({"get_account_info": _account_json()})
    reader = McpAccountReader(client)
    state = asyncio.run(reader.get_connection_state())
    assert state.connected is True


def test_get_connection_state_false_when_get_account_info_fails() -> None:
    client = _StubMcpClient({"get_account_info": _account_json()}, fail=frozenset({"get_account_info"}))
    reader = McpAccountReader(client)
    state = asyncio.run(reader.get_connection_state())
    assert state.connected is False
    assert "get_account_info failed" in state.detail


# ---------- ToolRegistry authorization enforcement ----------

def test_account_reader_only_calls_read_only_classified_tools() -> None:
    client = _StubMcpClient(
        {
            "get_account_info": _account_json(),
            "get_all_positions": REAL_POSITIONS_CSV,
            "get_all_pending_orders": REAL_ORDERS_CSV,
        }
    )
    reader = McpAccountReader(client)
    asyncio.run(reader.get_account_state())
    asyncio.run(reader.get_positions())
    asyncio.run(reader.get_orders())
    asyncio.run(reader.get_connection_state())
    assert len(client.calls) == 4
    for name, _ in client.calls:
        assert client.registry.classification_of(name) == ToolClass.READ_ONLY
