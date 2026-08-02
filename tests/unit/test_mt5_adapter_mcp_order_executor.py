"""
McpOrderExecutor tests against a stub McpClient -- per the Phase 6 planning entry in
docs/MCP_ADAPTER_WIRING_CHECKPOINT.md. No live MCP call is made anywhere in this file.

_StubMcpClient mirrors the established convention (see test_mt5_adapter_mcp_account.py):
re-implements McpClient.call_tool's registry.authorize_call(name) line against a REAL,
fully-classified registry -- this time with trading_enabled=True, since this executor IS
trading-capable -- so a bug that called an unclassified/wrong-class tool fails the test the
same way it would live.

Most tests use MockAccountReader (in-memory, pre-configured) for the account side, isolating
McpOrderExecutor's own logic. One dedicated test (test_registry_classification_end_to_end)
wires a real McpAccountReader onto the same stub client the executor uses, to prove every tool
call across both objects is correctly classified.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from mt5_mcp_trading.domain.models import AccountState, OrderPlan, PositionState
from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import ToolClass, ToolRegistry
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mt5_adapter.mcp_account import McpAccountReader
from mt5_mcp_trading.mt5_adapter.mcp_order_executor import ExecutionBlockedError, McpOrderExecutor
from mt5_mcp_trading.mt5_adapter.safety import NotDemoAccountError
from mt5_mcp_trading.state.store import StateStore

GRID_MAGIC = 71101


def _order_plan(**overrides: Any) -> OrderPlan:
    data: dict[str, Any] = dict(
        symbol="BTCUSD", order_type="LIMIT", side="BUY", volume=0.01, price=63000.0,
        sl=62000.0, tp=64000.0, deviation=150, magic=GRID_MAGIC, comment="grid_buy",
    )
    data.update(overrides)
    return OrderPlan(**data)


# Shape confirmed live in Phase 6 Step 4 (see docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md):
# `data` arrives as a POSITIONAL LIST, not a dict -- MT5's documented OrderSendResult field
# order (retcode, deal, order, volume, price, bid, ask, comment, request_id, retcode_external,
# request), the last element itself a nested, also-positional TradeRequest list. Before this
# was confirmed live, these fixtures used a dict shape, which parse_trade_response correctly
# refused with MalformedTradeResponseError rather than silently guessing -- exactly the
# "build from documented shape, verify live, correct if wrong" pattern this project follows.
_REQUEST_STUB = [5, 0, 0, "BTCUSD", 0.01, 63000.0, 0.0, 0.0, 0.0, 150, 2, 0, 0, 0, "MCP", 0, 0]

SUCCESS_PLACE_JSON = json.dumps({
    "error": False,
    "message": "Place pending order BUY_LIMIT BTCUSD 0.01 LOT at 63000.0 success (Order ID: 123456)",
    "data": [10009, 0, 123456, 0.01, 63000.0, 62999.0, 63001.0, "Request executed", 1, 0, _REQUEST_STUB],
})

# Confirmed regression case for the retcode-trust bug (docs/mcp_tool_classification.md, Known
# Issues item 7): the tool reports error:False / a message string containing "success", but the
# broker's own retcode says the trade was rejected. `data` is captured VERBATIM from a real
# Phase 6 Step 4 live run (AutoTrading disabled in the terminal, retcode 10027); `message` is
# reconstructed from place_pending_order.py's confirmed f-string
# (`f"Place pending order {order_type} {symbol} {volume} LOT at {price} success (Order ID:
# {response['data'].order})"`) with order=0, not independently captured, but not guessed either
# -- read directly from source this session.
LIVE_CAPTURED_AUTOTRADING_DISABLED_JSON = json.dumps({
    "error": False,
    "message": "Place pending order BUY_LIMIT BTCUSD 0.01 LOT at 57001.74 success (Order ID: 0)",
    "data": [
        10027, 0, 0, 0.0, 0.0, 0.0, 0.0, "AutoTrading disabled by client", 0, 0,
        [5, 0, 0, "BTCUSD", 0.01, 57001.74, 0.0, 0.0, 0.0, 20, 2, 0, 0, 0, "MCP", 0, 0],
    ],
})

PREFLIGHT_REJECTED_JSON = json.dumps({
    "error": True, "message": "Invalid price, must be above current ask", "data": None,
})

SUCCESS_CANCEL_JSON = json.dumps({
    "error": False,
    "message": "Cancel pending order 123456 success",
    "data": [10009, 0, 123456, 0.01, 63000.0, 0.0, 0.0, "Request executed", 1, 0, _REQUEST_STUB],
})


def _account_json() -> str:
    return json.dumps({"balance": 10000.0, "equity": 10000.0, "free_margin": 10000.0, "account_type": "real"})


EMPTY_POSITIONS_CSV = ",id,time,symbol,type,volume,open,stop_loss,take_profit,profit,magic,comment\n"
EMPTY_ORDERS_CSV = (
    ",id,time,symbol,type,volume,open,stop_loss,take_profit,state,type_time,expiration,magic,comment\n"
)


class _StubMcpClient:
    def __init__(
        self,
        responses: dict[str, str],
        registry: Optional[ToolRegistry] = None,
        fail: frozenset[str] = frozenset(),
    ) -> None:
        self._responses = responses
        self.registry = registry if registry is not None else build_metatrader_tool_registry(trading_enabled=True)
        self._fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> str:
        self.registry.authorize_call(name)
        self.calls.append((name, arguments or {}))
        if name in self._fail:
            raise ConnectionError(f"stub: {name} failed")
        return self._responses[name]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # _verify_present()'s retry backoff would otherwise slow every "ticket absent" test down
    # by up to ~1s for no benefit -- these are unit tests, not timing tests.
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("mt5_mcp_trading.mt5_adapter.mcp_order_executor.asyncio.sleep", _instant)


def _mock_account(
    positions: Optional[list[PositionState]] = None, orders: Optional[list] = None,
) -> MockAccountReader:
    return MockAccountReader(
        account_state=AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO"),
        positions=positions or [], orders=orders or [],
    )


class _SequencedAccountReader:
    """Returns different get_orders() results on successive calls -- call 1 (the pre-submit
    posture check) sees nothing yet; call 2+ (post-submit verification) sees the newly placed
    order. Mimics what actually happens live: the posture check runs before placement, the
    verification read runs after -- something a static MockAccountReader can't represent."""

    def __init__(self, account_state: AccountState, orders_sequence: list[list]) -> None:
        self._account_state = account_state
        self._orders_sequence = orders_sequence
        self._call_count = 0

    async def get_account_state(self) -> AccountState:
        return self._account_state

    async def get_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> list:
        return []

    async def get_orders(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> list:
        index = min(self._call_count, len(self._orders_sequence) - 1)
        self._call_count += 1
        return self._orders_sequence[index]

    async def get_connection_state(self):
        raise NotImplementedError


def _seed_open_record(store: StateStore, ticket: int) -> None:
    store.record_submission(
        ticket=ticket, strategy="grid", magic=GRID_MAGIC, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="LIMIT", requested_volume=0.01, requested_price=63000.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode="FOK", requested_expiry=None, retcode=10009,
        executed_price=63000.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )


# ---------- submit() ----------

def test_submit_success_records_intended_magic_not_mt5_zero(tmp_path: Path) -> None:
    from mt5_mcp_trading.domain.models import OrderState

    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    account_state = AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO")
    # Sequenced: the pre-submit posture check sees nothing (order doesn't exist yet); the
    # post-submit verification read sees it -- see _SequencedAccountReader's docstring.
    account = _SequencedAccountReader(account_state, orders_sequence=[
        [],
        [OrderState(ticket=123456, symbol="BTCUSD", side="BUY", volume=0.01, price=63000.0, magic=0)],
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))

    assert result.success is True
    assert result.retcode == 10009
    assert result.ticket == 123456
    assert result.verified is True

    record = store.lookup(123456)
    assert record is not None
    assert record.magic == GRID_MAGIC  # intended magic, not MT5's real magic=0
    assert record.strategy == "grid"
    assert record.status == "OPEN"
    assert client.calls == [
        ("place_pending_order", {
            "symbol": "BTCUSD", "volume": 0.01, "type": "BUY", "price": 63000.0,
            "stop_loss": 62000.0, "take_profit": 64000.0,
        })
    ]


def test_submit_rejected_retcode_is_trusted_over_tool_success_flag(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({"place_pending_order": LIVE_CAPTURED_AUTOTRADING_DISABLED_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))

    assert result.success is False
    assert result.retcode == 10027  # AutoTrading disabled -- NOT TRADE_RETCODE_DONE
    assert result.verified is False
    assert store.all_open() == ()  # never written -- a rejected order must not be recorded


def test_parse_trade_response_extracts_retcode_from_a_positional_list(tmp_path: Path) -> None:
    """Dedicated test for the list-shaped data confirmed live (see this file's fixture
    comments) -- proves the positional field mapping is correct, not just that *some*
    rejection is detected."""
    from mt5_mcp_trading.mt5_adapter.metatrader_retcodes import parse_trade_response

    response = parse_trade_response(LIVE_CAPTURED_AUTOTRADING_DISABLED_JSON)
    assert response.retcode == 10027
    assert response.done is False
    assert response.raw_data is not None
    assert response.raw_data["comment"] == "AutoTrading disabled by client"
    assert response.raw_data["order"] == 0


def test_submit_preflight_rejection_has_no_retcode(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({"place_pending_order": PREFLIGHT_REJECTED_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))

    assert result.success is False
    assert result.retcode == -1  # sentinel: no real MT5 retcode was ever produced
    assert store.all_open() == ()


def test_submit_verification_fails_when_ticket_absent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    account = _mock_account(orders=[])  # ticket 123456 never shows up on read-back
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))

    assert result.success is True  # placement itself was confirmed done
    assert result.verified is False  # but re-reading real state couldn't confirm it
    assert "NOT found" in result.verification_details


def test_submit_only_supports_limit_orders_in_this_step(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(NotImplementedError):
        asyncio.run(executor.submit(_order_plan(order_type="MARKET")))
    assert client.calls == []


def test_require_demo_account_kind_blocks_submit_before_any_mcp_call(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="REAL")

    with pytest.raises(NotDemoAccountError):
        asyncio.run(executor.submit(_order_plan()))
    assert client.calls == []


def test_manage_only_posture_blocks_new_submission(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    # A real position with no local record at all -- "cannot be matched safely".
    account = _mock_account(positions=[
        PositionState(ticket=999999, symbol="BTCUSD", side="BUY", volume=0.02, price_open=64000.0, profit=0.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.submit(_order_plan()))
    assert client.calls == []  # never reached place_pending_order


def test_blocked_posture_blocks_submission_on_corrupted_state_file(tmp_path: Path) -> None:
    path = tmp_path / "order_state.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = StateStore(path)
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.submit(_order_plan()))
    assert client.calls == []


# ---------- cancel() ----------

def test_cancel_success_transitions_state_to_cancelled(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _seed_open_record(store, 123456)
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON})
    account = _mock_account(orders=[])  # confirmed absent after cancelling
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.cancel(123456))

    assert result.success is True
    assert result.verified is True
    assert store.lookup(123456).status == "CANCELLED"  # type: ignore[union-attr]
    assert client.calls == [("cancel_pending_order", {"id": 123456})]


def test_require_demo_account_kind_blocks_cancel_before_any_mcp_call(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _seed_open_record(store, 123456)
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind=None)

    with pytest.raises(NotDemoAccountError):
        asyncio.run(executor.cancel(123456))
    assert client.calls == []


def test_manage_only_posture_allows_cancel_of_a_matched_ticket(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _seed_open_record(store, 123456)  # locally known
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON})
    account = _mock_account(positions=[
        PositionState(ticket=123456, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=0.0, magic=0),
        PositionState(ticket=999999, symbol="BTCUSD", side="SELL", volume=0.02, price_open=64000.0, profit=0.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.cancel(123456))  # matched ticket -- allowed despite MANAGE_ONLY

    assert result.success is True
    assert client.calls == [("cancel_pending_order", {"id": 123456})]


def test_manage_only_posture_blocks_cancel_of_an_unattributed_ticket(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _seed_open_record(store, 123456)
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON})
    account = _mock_account(positions=[
        PositionState(ticket=123456, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=0.0, magic=0),
        PositionState(ticket=999999, symbol="BTCUSD", side="SELL", volume=0.02, price_open=64000.0, profit=0.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.cancel(999999))  # unknown_real -- never attributed, never touched
    assert client.calls == []


def test_blocked_posture_blocks_cancel_on_corrupted_state_file(tmp_path: Path) -> None:
    path = tmp_path / "order_state.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = StateStore(path)
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.cancel(123456))
    assert client.calls == []


# ---------- close_position() ----------

def test_close_position_not_implemented_in_this_step(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(NotImplementedError):
        asyncio.run(executor.close_position(123456))
    assert client.calls == []


# ---------- ToolRegistry authorization enforcement ----------

def test_registry_classification_end_to_end(tmp_path: Path) -> None:
    """One stub client shared by a real McpAccountReader and McpOrderExecutor -- proves every
    tool call across both is correctly classified (TRADING for mutations, READ_ONLY for
    verification/account reads), the same way it would live."""
    store = StateStore(tmp_path / "order_state.json")
    client = _StubMcpClient({
        "get_account_info": _account_json(),
        "get_positions_with_magic": EMPTY_POSITIONS_CSV,
        "get_pending_orders_with_magic": EMPTY_ORDERS_CSV,
        "place_pending_order": SUCCESS_PLACE_JSON,
        "cancel_pending_order": SUCCESS_CANCEL_JSON,
    })
    account = McpAccountReader(client)
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    asyncio.run(executor.submit(_order_plan()))
    asyncio.run(executor.cancel(123456))

    assert len(client.calls) >= 4
    mutating = {"place_pending_order", "cancel_pending_order"}
    for name, _ in client.calls:
        expected = ToolClass.TRADING if name in mutating else ToolClass.READ_ONLY
        assert client.registry.classification_of(name) == expected, name
