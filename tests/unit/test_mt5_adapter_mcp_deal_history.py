"""
Tests McpDealHistoryReader against a stub McpClient rather than a live MCP session -- same
approach as test_mt5_adapter_mcp_market_data.py. No live MCP call is made anywhere in this
file.

_StubMcpClient deliberately re-implements McpClient.call_tool's one safety-critical line
(`registry.authorize_call(name)`) using the real, fully-classified registry from
metatrader_tools.build_metatrader_tool_registry() -- a future change that made
McpDealHistoryReader call an unclassified or TRADING-classified tool would make these tests
raise, the same way it would against a real McpClient.

The sample CSV below matches the exact shape traced from source (Phase 9 Step 4/5 research;
see domain/models.py's Deal docstring and mcp_deal_history.py's module docstring): get_deals's
underlying DataFrame calls set_index("time"), so the CSV's leading column is named "time" (not
blank, unlike candles/positions CSVs), and includes real MT5 columns this reader doesn't need
(time_msc, reason, external_id) -- present here to prove they're ignored, not required, same
pattern test_get_symbol_info_parses_broker_specs already established for extra fields.
"""

from __future__ import annotations

import asyncio
from datetime import timezone
from typing import Any, Optional

import pytest

from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import ToolClass, ToolRegistry
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader

REAL_DEALS_CSV = (
    "time,ticket,order,time_msc,type,entry,magic,position_id,reason,volume,price,commission,"
    "swap,profit,fee,symbol,comment,external_id\n"
    "2026-08-04 10:15:32,171618037,171618036,1754301332123,1,1,0,171618036,0,0.01,63500.25,"
    "-0.06,0.0,12.34,0.0,BTCUSD,,\n"
)

EMPTY_DEALS_CSV = "\n"


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


# ---------- get_deals ----------

def test_get_deals_parses_real_fields() -> None:
    client = _StubMcpClient({"get_deals": REAL_DEALS_CSV})
    reader = McpDealHistoryReader(client)
    deals = asyncio.run(reader.get_deals())
    assert len(deals) == 1
    deal = deals[0]
    assert (deal.ticket, deal.order, deal.position_id) == (171618037, 171618036, 171618036)
    assert (deal.type, deal.entry) == (1, 1)  # raw ints, not strings
    assert deal.symbol == "BTCUSD"
    assert (deal.volume, deal.price, deal.profit) == (0.01, 63500.25, 12.34)
    assert (deal.commission, deal.swap, deal.fee) == (-0.06, 0.0, 0.0)
    assert deal.magic == 0  # parsed and carried, even though callers must not trust it
    assert deal.comment == ""


def test_get_deals_attaches_utc_to_the_naturally_naive_time_field() -> None:
    # get_deals_as_dataframe converts MT5's epoch-seconds time via pd.to_datetime(unit="s"),
    # which is a real UTC instant but arrives with no offset suffix at all -- unlike every
    # other timestamp this codebase parses. Proves the reader attaches tzinfo explicitly
    # rather than silently returning a naive datetime.
    client = _StubMcpClient({"get_deals": REAL_DEALS_CSV})
    reader = McpDealHistoryReader(client)
    deal = asyncio.run(reader.get_deals())[0]
    assert deal.time.tzinfo is not None
    assert deal.time.utcoffset() == timezone.utc.utcoffset(None)
    assert deal.time.isoformat() == "2026-08-04T10:15:32+00:00"


def test_get_deals_ignores_extra_real_mt5_columns() -> None:
    # time_msc/reason/external_id are real columns this reader doesn't model -- must not raise.
    client = _StubMcpClient({"get_deals": REAL_DEALS_CSV})
    reader = McpDealHistoryReader(client)
    deals = asyncio.run(reader.get_deals())
    assert len(deals) == 1


def test_get_deals_returns_empty_list_for_no_history() -> None:
    client = _StubMcpClient({"get_deals": EMPTY_DEALS_CSV})
    reader = McpDealHistoryReader(client)
    assert asyncio.run(reader.get_deals()) == []


def test_get_deals_raises_on_malformed_csv_missing_required_column() -> None:
    malformed = "time,ticket,order,type,entry,magic,position_id,volume,price\n" \
        "2026-08-04 10:15:32,171618037,171618036,1,1,0,171618036,0.01,63500.25\n"  # no commission/swap/fee/symbol/profit/comment
    client = _StubMcpClient({"get_deals": malformed})
    reader = McpDealHistoryReader(client)
    with pytest.raises(KeyError):
        asyncio.run(reader.get_deals())


def test_get_deals_raises_on_non_numeric_field() -> None:
    malformed = (
        "time,ticket,order,type,entry,magic,position_id,volume,price,commission,swap,profit,"
        "fee,symbol,comment\n"
        "2026-08-04 10:15:32,171618037,171618036,1,1,0,171618036,0.01,not-a-number,-0.06,0.0,"
        "12.34,0.0,BTCUSD,\n"
    )
    client = _StubMcpClient({"get_deals": malformed})
    reader = McpDealHistoryReader(client)
    with pytest.raises(ValueError):
        asyncio.run(reader.get_deals())


# ---------- argument passing ----------

def test_get_deals_sends_no_arguments_when_all_none() -> None:
    client = _StubMcpClient({"get_deals": EMPTY_DEALS_CSV})
    reader = McpDealHistoryReader(client)
    asyncio.run(reader.get_deals())
    assert client.calls == [("get_deals", {})]


def test_get_deals_sends_only_the_provided_arguments() -> None:
    client = _StubMcpClient({"get_deals": EMPTY_DEALS_CSV})
    reader = McpDealHistoryReader(client)
    asyncio.run(reader.get_deals(from_date="2026-08-01", symbol="BTCUSD"))
    assert client.calls == [
        ("get_deals", {"from_date": "2026-08-01", "symbol": "BTCUSD"})
    ]


# ---------- ToolRegistry authorization enforcement ----------

def test_deal_history_reader_only_calls_read_only_classified_tools() -> None:
    client = _StubMcpClient({"get_deals": REAL_DEALS_CSV})
    reader = McpDealHistoryReader(client)
    asyncio.run(reader.get_deals())
    assert len(client.calls) == 1
    for name, _ in client.calls:
        assert client.registry.classification_of(name) == ToolClass.READ_ONLY
