"""
McpOrderExecutor tests against a stub McpClient -- see
docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md. No live MCP call is made anywhere in this
file.

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

from mt5_mcp_trading.domain.models import AccountState, OrderPlan, OrderState, PositionState
from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import ToolClass, ToolRegistry
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mt5_adapter.mcp_account import McpAccountReader
from mt5_mcp_trading.mt5_adapter.mcp_order_executor import (
    ExecutionBlockedError,
    InvalidOrderPlanError,
    McpOrderExecutor,
    SlTpAttachmentFailedError,
)
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

# close_position's response shape mirrors send_order()'s DEAL branch -- same OrderSendResult
# positional list already confirmed live in Step 4. `deal` (index 1) is a real deal ticket
# here since a close is itself a DEAL execution, unlike place/cancel where it's 0.
SUCCESS_CLOSE_JSON = json.dumps({
    "error": False,
    "message": "Close position 555555 success at price 63050.0",
    "data": [10009, 987654, 0, 0.01, 63050.0, 63049.0, 63051.0, "Request executed", 1, 0, _REQUEST_STUB],
})

CLOSE_REJECTED_JSON = json.dumps({
    "error": False,
    "message": "Order sent successfully",
    "data": [10018, 0, 0, 0.01, 0.0, 0.0, 0.0, "Market closed", 0, 0, _REQUEST_STUB],
})

CLOSE_INVALID_POSITION_ID_JSON = json.dumps({
    "error": True, "message": "Invalid position ID '555555'", "data": None,
})

# place_market_order's response mirrors send_order()'s DEAL branch -- same OrderSendResult
# positional list already confirmed live in Step 4/5. "order" (index 2) is the resulting
# position's ticket for an immediately-filled market execution on this (hedging-style, per
# Steps 4-5) account.
SUCCESS_MARKET_PLACE_JSON = json.dumps({
    "error": False,
    "message": "BUY BTCUSD 0.01 LOT at 63005.0 success (Position ID: 555777)",
    "data": [10009, 987001, 555777, 0.01, 63005.0, 63004.0, 63006.0, "Request executed", 1, 0, _REQUEST_STUB],
})

MARKET_PLACE_REJECTED_JSON = json.dumps({
    "error": False,
    "message": "Order sent successfully",
    "data": [10018, 0, 0, 0.01, 0.0, 0.0, 0.0, "Market closed", 0, 0, _REQUEST_STUB],
})

# modify_position's response reuses the same shape via send_order()'s SLTP branch -- no new
# order/deal is created, just the position modified, hence order=0/deal=0 here.
SUCCESS_MODIFY_POSITION_JSON = json.dumps({
    "error": False,
    "message": "Modify position 555777 success, SL at 62000.0, TP at 64000.0, current price 63005.0",
    "data": [10009, 0, 0, 0.0, 63005.0, 0.0, 0.0, "Request executed", 1, 0, _REQUEST_STUB],
})

# Confirmed-possible clean rejection (docs/mcp_tool_classification.md item 7 addendum): SL/TP
# too close to price relative to the broker's stops_level -- not pre-validated locally in this
# step (see mcp_order_executor.py's module docstring), so this must be handled like any other
# trusted-retcode rejection.
MODIFY_POSITION_REJECTED_JSON = json.dumps({
    "error": False,
    "message": "Order sent successfully",
    "data": [10016, 0, 0, 0.0, 0.0, 0.0, 0.0, "Invalid stops", 0, 0, _REQUEST_STUB],
})

# Phase 6 Step 7: SELL-side fixtures -- mirrors the BUY fixtures above exactly, just the
# opposite side, to close the SELL-branch test gap (_validate_market_sl_tp's `else:` clause and
# the full SELL flow through _submit_market() were never exercised by any test until now).
SUCCESS_MARKET_SELL_PLACE_JSON = json.dumps({
    "error": False,
    "message": "SELL BTCUSD 0.01 LOT at 62875.0 success (Position ID: 555778)",
    "data": [10009, 987002, 555778, 0.01, 62875.0, 62874.0, 62876.0, "Request executed", 1, 0, _REQUEST_STUB],
})

SUCCESS_MODIFY_POSITION_SELL_JSON = json.dumps({
    "error": False,
    "message": "Modify position 555778 success, SL at 64000.0, TP at 62000.0, current price 62875.0",
    "data": [10009, 0, 0, 0.0, 62875.0, 0.0, 0.0, "Request executed", 1, 0, _REQUEST_STUB],
})


def _account_json() -> str:
    return json.dumps({"balance": 10000.0, "equity": 10000.0, "free_margin": 10000.0, "account_type": "real"})


EMPTY_POSITIONS_CSV = ",id,time,symbol,type,volume,open,stop_loss,take_profit,profit,magic,comment\n"
EMPTY_ORDERS_CSV = (
    ",id,time,symbol,type,volume,open,stop_loss,take_profit,state,type_time,expiration,magic,comment\n"
)
# unknown_real-explanation's own extra get_deals() read (see _explain_unknown_real()) -- an
# empty result means "no evidence found", so the ticket stays unexplained/unknown_real, exactly
# as if the check didn't exist. Used by every MANAGE_ONLY test below that has a non-empty
# local_open set (the one case where that extra read is actually attempted -- see
# McpOrderExecutor._current_posture()'s local_open short-circuit). Header shape matches
# test_mt5_adapter_mcp_deal_history.py's REAL_DEALS_CSV (get_deals's DataFrame is indexed by
# "time", unlike positions/orders' blank index column) -- irrelevant for an empty body, kept
# consistent anyway rather than inventing a different shape.
EMPTY_DEALS_CSV = (
    "time,ticket,order,time_msc,type,entry,magic,position_id,reason,volume,price,commission,"
    "swap,profit,fee,symbol,comment,external_id\n"
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


class _SequencedPositionsAccountReader:
    """Same idea as _SequencedAccountReader but drives get_positions() through a sequence --
    needed for MARKET submit tests, which read live positions three times in one call: the
    pre-submit posture check, the post-place verify_position_present, and the post-attach
    verify_sl_tp_attached. A static MockAccountReader can't represent sl/tp changing across
    those three reads the way a real modify_position call would."""

    def __init__(self, account_state: AccountState, positions_sequence: list[list]) -> None:
        self._account_state = account_state
        self._positions_sequence = positions_sequence
        self._call_count = 0

    async def get_account_state(self) -> AccountState:
        return self._account_state

    async def get_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> list:
        index = min(self._call_count, len(self._positions_sequence) - 1)
        self._call_count += 1
        return self._positions_sequence[index]

    async def get_orders(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> list:
        return []

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

    store = StateStore(tmp_path / "order_state")
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
    store = StateStore(tmp_path / "order_state")
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
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({"place_pending_order": PREFLIGHT_REJECTED_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))

    assert result.success is False
    assert result.retcode == -1  # sentinel: no real MT5 retcode was ever produced
    assert store.all_open() == ()


def test_submit_verification_fails_when_ticket_absent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    account = _mock_account(orders=[])  # ticket 123456 never shows up on read-back
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))

    assert result.success is True  # placement itself was confirmed done
    assert result.verified is False  # but re-reading real state couldn't confirm it
    assert "NOT found" in result.verification_details


def test_submit_rejects_unsupported_order_types(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(NotImplementedError):
        asyncio.run(executor.submit(_order_plan(order_type="STOP_LIMIT")))
    assert client.calls == []


# ---------- submit() MARKET (Phase 6 Step 6) ----------

def test_submit_market_success_places_and_attaches_protection(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({
        "place_market_order": SUCCESS_MARKET_PLACE_JSON,
        "modify_position": SUCCESS_MODIFY_POSITION_JSON,
    })
    account_state = AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO")
    account = _SequencedPositionsAccountReader(account_state, positions_sequence=[
        [],  # pre-submit posture check: nothing exists yet
        [PositionState(ticket=555777, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63005.0, profit=0.0, magic=0, sl=0.0, tp=0.0)],  # verify_position_present, before attach
        [PositionState(ticket=555777, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63005.0, profit=0.0, magic=0, sl=62000.0, tp=64000.0)],  # verify_sl_tp_attached, after attach
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan(order_type="MARKET")))

    assert result.success is True
    assert result.retcode == 10009
    assert result.ticket == 555777
    assert result.verified is True

    record = store.lookup(555777)
    assert record is not None
    assert record.status == "OPEN"  # transitioned from OPEN_UNPROTECTED once attach confirmed
    assert record.magic == GRID_MAGIC
    assert [name for name, _ in client.calls] == ["place_market_order", "modify_position"]
    assert client.calls[0] == ("place_market_order", {"symbol": "BTCUSD", "volume": 0.01, "type": "BUY"})
    assert client.calls[1] == ("modify_position", {"id": 555777, "stop_loss": 62000.0, "take_profit": 64000.0})


def test_submit_market_rejects_missing_sl_tp_before_any_mcp_call(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(InvalidOrderPlanError):
        asyncio.run(executor.submit(_order_plan(order_type="MARKET", sl=0.0, tp=0.0)))
    assert client.calls == []


def test_submit_market_rejects_wrong_side_sl_tp_before_any_mcp_call(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    # BUY with sl/tp swapped -- sl above price, tp below -- must never reach MT5.
    with pytest.raises(InvalidOrderPlanError):
        asyncio.run(executor.submit(_order_plan(order_type="MARKET", sl=64000.0, tp=62000.0)))
    assert client.calls == []


def test_submit_market_place_rejected_no_modify_attempted(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({"place_market_order": MARKET_PLACE_REJECTED_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan(order_type="MARKET")))

    assert result.success is False
    assert result.retcode == 10018  # market closed -- NOT done
    assert store.all_open() == ()  # nothing was opened, nothing to protect
    assert [name for name, _ in client.calls] == ["place_market_order"]  # modify_position never called


def test_submit_market_attach_rejected_marks_open_unprotected(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({
        "place_market_order": SUCCESS_MARKET_PLACE_JSON,
        "modify_position": MODIFY_POSITION_REJECTED_JSON,
    })
    account_state = AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO")
    account = _SequencedPositionsAccountReader(account_state, positions_sequence=[
        [],
        [PositionState(ticket=555777, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63005.0, profit=0.0, magic=0, sl=0.0, tp=0.0)],
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(SlTpAttachmentFailedError) as exc_info:
        asyncio.run(executor.submit(_order_plan(order_type="MARKET")))
    assert exc_info.value.ticket == 555777
    assert exc_info.value.retcode == 10016

    record = store.lookup(555777)
    assert record is not None
    assert record.status == "OPEN_UNPROTECTED"  # position is real, open, and unprotected

    modify_calls = [c for c in client.calls if c[0] == "modify_position"]
    assert len(modify_calls) == 1  # exactly one attempt -- no retry
    assert not any(name in ("close_position", "cancel_pending_order") for name, _ in client.calls)  # no auto-remediation


def test_submit_market_attach_call_raises_marks_open_unprotected(tmp_path: Path) -> None:
    """The process-dies-mid-sequence scenario: proves state was written BEFORE the attach
    attempt, not after -- the single most important guarantee in this step."""
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient(
        {"place_market_order": SUCCESS_MARKET_PLACE_JSON},
        fail=frozenset({"modify_position"}),
    )
    account_state = AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO")
    account = _SequencedPositionsAccountReader(account_state, positions_sequence=[
        [],
        [PositionState(ticket=555777, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63005.0, profit=0.0, magic=0, sl=0.0, tp=0.0)],
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(SlTpAttachmentFailedError):
        asyncio.run(executor.submit(_order_plan(order_type="MARKET")))

    record = store.lookup(555777)
    assert record is not None
    assert record.status == "OPEN_UNPROTECTED"


def test_submit_market_attach_retcode_done_but_live_read_disagrees(tmp_path: Path) -> None:
    """Retcode says done, but a fresh live read of the position's actual sl/tp disagrees --
    must still be treated as attachment failure, never trusted on retcode alone."""
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({
        "place_market_order": SUCCESS_MARKET_PLACE_JSON,
        "modify_position": SUCCESS_MODIFY_POSITION_JSON,
    })
    account_state = AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO")
    account = _SequencedPositionsAccountReader(account_state, positions_sequence=[
        [],
        [PositionState(ticket=555777, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63005.0, profit=0.0, magic=0, sl=0.0, tp=0.0)],
        # live sl/tp never actually change despite the confirmed-done retcode above
        [PositionState(ticket=555777, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63005.0, profit=0.0, magic=0, sl=0.0, tp=0.0)],
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(SlTpAttachmentFailedError):
        asyncio.run(executor.submit(_order_plan(order_type="MARKET")))

    assert store.lookup(555777).status == "OPEN_UNPROTECTED"  # type: ignore[union-attr]


def test_open_unprotected_ticket_does_not_block_other_submissions(tmp_path: Path) -> None:
    """Decision (approved): OPEN_UNPROTECTED blocks only its own ticket, never the whole
    executor -- an unrelated LIMIT submission must still succeed normally."""
    store = StateStore(tmp_path / "order_state")
    store.record_submission(
        ticket=555777, strategy="grid", magic=GRID_MAGIC, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="MARKET", requested_volume=0.01, requested_price=63005.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode=None, requested_expiry=None, retcode=10009,
        executed_price=63005.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=datetime(2026, 8, 2, tzinfo=timezone.utc), status="OPEN_UNPROTECTED",
    )
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    account_state = AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO")
    account = _SequencedAccountReader(account_state, orders_sequence=[
        [],
        [OrderState(ticket=123456, symbol="BTCUSD", side="BUY", volume=0.01, price=63000.0, magic=0)],
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))  # unrelated LIMIT order

    assert result.success is True  # not blocked by ticket 555777's OPEN_UNPROTECTED status
    assert store.lookup(555777).status == "OPEN_UNPROTECTED"  # type: ignore[union-attr]  -- untouched


def test_open_unprotected_ticket_allows_explicit_protective_close(tmp_path: Path) -> None:
    """The one recovery path this project allows: an explicit, human-approved close_position()
    call must still work normally on an OPEN_UNPROTECTED ticket."""
    store = StateStore(tmp_path / "order_state")
    store.record_submission(
        ticket=555777, strategy="grid", magic=GRID_MAGIC, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="MARKET", requested_volume=0.01, requested_price=63005.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode=None, requested_expiry=None, retcode=10009,
        executed_price=63005.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=datetime(2026, 8, 2, tzinfo=timezone.utc), status="OPEN_UNPROTECTED",
    )
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON})
    account = _mock_account(positions=[
        PositionState(ticket=555777, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63005.0, profit=0.0, magic=0, sl=0.0, tp=0.0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.close_position(555777))

    assert result.success is True
    assert client.calls == [("close_position", {"id": 555777})]


# ---------- submit() MARKET SELL-side (Phase 6 Step 7) ----------
# Step 6 only exercised BUY -- _validate_market_sl_tp()'s `else:` (SELL) branch and the full
# SELL flow through _submit_market() had zero test coverage, mocked or live, until now.

def test_submit_market_sell_success_places_and_attaches_protection(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({
        "place_market_order": SUCCESS_MARKET_SELL_PLACE_JSON,
        "modify_position": SUCCESS_MODIFY_POSITION_SELL_JSON,
    })
    account_state = AccountState(balance=10000.0, equity=10000.0, margin_free=10000.0, trade_mode="DEMO")
    account = _SequencedPositionsAccountReader(account_state, positions_sequence=[
        [],  # pre-submit posture check
        [PositionState(ticket=555778, symbol="BTCUSD", side="SELL", volume=0.01, price_open=62875.0, profit=0.0, magic=0, sl=0.0, tp=0.0)],  # verify_position_present
        [PositionState(ticket=555778, symbol="BTCUSD", side="SELL", volume=0.01, price_open=62875.0, profit=0.0, magic=0, sl=64000.0, tp=62000.0)],  # verify_sl_tp_attached
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    # SELL: tp < price < sl (opposite of BUY) -- sl above price, tp below.
    result = asyncio.run(executor.submit(
        _order_plan(order_type="MARKET", side="SELL", sl=64000.0, tp=62000.0)
    ))

    assert result.success is True
    assert result.retcode == 10009
    assert result.ticket == 555778
    assert result.verified is True

    record = store.lookup(555778)
    assert record is not None
    assert record.status == "OPEN"
    assert [name for name, _ in client.calls] == ["place_market_order", "modify_position"]
    assert client.calls[0] == ("place_market_order", {"symbol": "BTCUSD", "volume": 0.01, "type": "SELL"})
    assert client.calls[1] == ("modify_position", {"id": 555778, "stop_loss": 64000.0, "take_profit": 62000.0})


def test_submit_market_rejects_wrong_side_sl_tp_for_sell_before_any_mcp_call(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    # SELL with sl/tp in BUY order (sl below price, tp above) -- must never reach MT5.
    with pytest.raises(InvalidOrderPlanError):
        asyncio.run(executor.submit(
            _order_plan(order_type="MARKET", side="SELL", sl=62000.0, tp=64000.0)
        ))
    assert client.calls == []


def test_require_demo_account_kind_blocks_submit_before_any_mcp_call(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="REAL")

    with pytest.raises(NotDemoAccountError):
        asyncio.run(executor.submit(_order_plan()))
    assert client.calls == []


def test_manage_only_posture_blocks_new_submission(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
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
    path = tmp_path / "order_state"
    path.mkdir()
    (path / "999999.json").write_text("{not valid json", encoding="utf-8")
    store = StateStore(path)
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.submit(_order_plan()))
    assert client.calls == []


# ---------- cancel() ----------

def test_cancel_success_transitions_state_to_cancelled(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
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
    store = StateStore(tmp_path / "order_state")
    _seed_open_record(store, 123456)
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind=None)

    with pytest.raises(NotDemoAccountError):
        asyncio.run(executor.cancel(123456))
    assert client.calls == []


def test_manage_only_posture_allows_cancel_of_a_matched_ticket(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_record(store, 123456)  # locally known
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON, "get_deals": EMPTY_DEALS_CSV})
    account = _mock_account(positions=[
        PositionState(ticket=123456, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=0.0, magic=0),
        PositionState(ticket=999999, symbol="BTCUSD", side="SELL", volume=0.02, price_open=64000.0, profit=0.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.cancel(123456))  # matched ticket -- allowed despite MANAGE_ONLY

    assert result.success is True
    # 999999's unknown_real triggers one extra, unexplaining get_deals() read (empty result --
    # see EMPTY_DEALS_CSV) before the matched ticket's own cancel proceeds normally.
    assert [name for name, _ in client.calls] == ["get_deals", "cancel_pending_order"]
    assert client.calls[1] == ("cancel_pending_order", {"id": 123456})


def test_manage_only_posture_blocks_cancel_of_an_unattributed_ticket(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_record(store, 123456)
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON, "get_deals": EMPTY_DEALS_CSV})
    account = _mock_account(positions=[
        PositionState(ticket=123456, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=0.0, magic=0),
        PositionState(ticket=999999, symbol="BTCUSD", side="SELL", volume=0.02, price_open=64000.0, profit=0.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.cancel(999999))  # unknown_real -- never attributed, never touched
    # The unexplaining get_deals() read still happens (see above), but no mutating call ever
    # does -- cancel_pending_order is never reached.
    assert [name for name, _ in client.calls] == ["get_deals"]


def test_blocked_posture_blocks_cancel_on_corrupted_state_file(tmp_path: Path) -> None:
    path = tmp_path / "order_state"
    path.mkdir()
    (path / "999999.json").write_text("{not valid json", encoding="utf-8")
    store = StateStore(path)
    client = _StubMcpClient({"cancel_pending_order": SUCCESS_CANCEL_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.cancel(123456))
    assert client.calls == []


# ---------- close_position() ----------

def _seed_open_position_record(store: StateStore, ticket: int) -> None:
    # A LocalOrderRecord seeded as if it originated from a MARKET fill -- close_position()
    # doesn't care how the position was opened, only that a local record exists for it.
    store.record_submission(
        ticket=ticket, strategy="grid", magic=GRID_MAGIC, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="MARKET", requested_volume=0.01, requested_price=None,
        requested_sl=0.0, requested_tp=0.0, requested_deviation=150,
        requested_filling_mode=None, requested_expiry=None, retcode=10009,
        executed_price=63000.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )


def test_close_position_success_closes_full_position_and_records_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON})
    account = _mock_account(positions=[])  # confirmed absent after closing
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.close_position(555555))

    assert result.success is True
    assert result.retcode == 10009
    assert result.ticket == 555555
    assert result.deal == 987654
    assert result.executed_price == 63050.0
    assert result.verified is True
    assert store.lookup(555555).status == "CLOSED"  # type: ignore[union-attr]
    assert client.calls == [("close_position", {"id": 555555})]


def test_close_position_with_matching_full_volume_is_allowed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON})
    account = _mock_account(positions=[
        PositionState(ticket=555555, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=5.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.close_position(555555, volume=0.01))  # matches full size

    assert result.success is True
    assert client.calls == [("close_position", {"id": 555555})]


def test_close_position_rejects_a_partial_volume_request(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON})
    account = _mock_account(positions=[
        PositionState(ticket=555555, symbol="BTCUSD", side="BUY", volume=0.03, price_open=63000.0, profit=5.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(NotImplementedError):
        asyncio.run(executor.close_position(555555, volume=0.01))  # 0.01 != full 0.03
    assert client.calls == []  # never reached close_position -- caught before any MCP call


def test_close_position_rejected_retcode_is_trusted(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)
    client = _StubMcpClient({"close_position": CLOSE_REJECTED_JSON})
    account = _mock_account(positions=[
        PositionState(ticket=555555, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=5.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.close_position(555555))

    assert result.success is False
    assert result.retcode == 10018  # TRADE_RETCODE_MARKET_CLOSED -- NOT done
    assert result.verified is False
    assert store.lookup(555555).status == "OPEN"  # unchanged -- a rejected close is never recorded


def test_close_position_invalid_position_id_has_no_retcode(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({"close_position": CLOSE_INVALID_POSITION_ID_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.close_position(999999))  # never existed locally either

    assert result.success is False
    assert result.retcode == -1  # sentinel: close_position couldn't even find the position


def test_close_position_verification_fails_when_still_present(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON})
    account = _mock_account(positions=[
        PositionState(ticket=555555, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=5.0, magic=0),
    ])  # still present on read-back despite a confirmed-done retcode
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.close_position(555555))

    assert result.success is True  # the close itself was confirmed done
    assert result.verified is False  # but re-reading real state still shows it
    assert "still present" in result.verification_details


def test_require_demo_account_kind_blocks_close_before_any_mcp_call(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="REAL")

    with pytest.raises(NotDemoAccountError):
        asyncio.run(executor.close_position(555555))
    assert client.calls == []


def test_manage_only_posture_allows_close_of_a_matched_ticket(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)  # locally known
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON, "get_deals": EMPTY_DEALS_CSV})
    account = _mock_account(positions=[
        PositionState(ticket=555555, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=5.0, magic=0),
        PositionState(ticket=999999, symbol="BTCUSD", side="SELL", volume=0.02, price_open=64000.0, profit=0.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.close_position(555555))  # matched -- allowed despite MANAGE_ONLY

    assert result.success is True
    # 999999's unknown_real triggers one extra, unexplaining get_deals() read (empty result --
    # see EMPTY_DEALS_CSV) before the matched ticket's own close proceeds normally.
    assert [name for name, _ in client.calls] == ["get_deals", "close_position"]
    assert client.calls[1] == ("close_position", {"id": 555555})


def test_manage_only_posture_blocks_close_of_an_unattributed_ticket(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _seed_open_position_record(store, 555555)
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON, "get_deals": EMPTY_DEALS_CSV})
    account = _mock_account(positions=[
        PositionState(ticket=555555, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, profit=5.0, magic=0),
        PositionState(ticket=999999, symbol="BTCUSD", side="SELL", volume=0.02, price_open=64000.0, profit=0.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.close_position(999999))  # unknown_real -- never touched
    # The unexplaining get_deals() read still happens (see above), but no mutating call ever
    # does -- close_position is never reached.
    assert [name for name, _ in client.calls] == ["get_deals"]


def test_blocked_posture_blocks_close_on_corrupted_state_file(tmp_path: Path) -> None:
    path = tmp_path / "order_state"
    path.mkdir()
    (path / "999999.json").write_text("{not valid json", encoding="utf-8")
    store = StateStore(path)
    client = _StubMcpClient({"close_position": SUCCESS_CLOSE_JSON})
    executor = McpOrderExecutor(client, _mock_account(), store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.close_position(555555))
    assert client.calls == []


# ---------- unknown_real SL/TP-close-artifact explanation (Phase 9, root-caused live 2026-08-10) ----------
# Full-stack integration coverage for McpOrderExecutor._explain_unknown_real() -- state/
# sl_tp_artifact.py's own pure-function evidence rules are covered exhaustively in
# tests/unit/test_state_sl_tp_artifact.py; these tests instead prove the wiring: a real get_deals()
# read happens at the right time, an explained ticket unblocks the executor AND reconciles its
# underlying position to CLOSED, an unexplained ticket still blocks exactly as before, and a
# failure gathering evidence fails closed exactly like a genuinely-foreign ticket would.

_SL_ARTIFACT_DEALS_CSV_HEADER = (
    "time,ticket,order,time_msc,type,entry,magic,position_id,reason,volume,price,commission,"
    "swap,profit,fee,symbol,comment,external_id\n"
)


def _seed_grid_position(
    store: StateStore, ticket: int, *, side: str, requested_sl: float, requested_tp: float,
) -> None:
    store.record_submission(
        ticket=ticket, strategy="grid", magic=GRID_MAGIC, comment="grid_sell" if side == "SELL" else "grid_buy",
        symbol="BTCUSD", side=side, order_type="LIMIT", requested_volume=0.01,
        requested_price=65150.0, requested_sl=requested_sl, requested_tp=requested_tp,
        requested_deviation=150, requested_filling_mode="FOK", requested_expiry=None,
        retcode=10009, executed_price=65150.0, executed_volume=0.01,
        broker_comment="Request executed",
        submitted_at=datetime(2026, 8, 10, 2, 18, 17, tzinfo=timezone.utc),
    )


def test_unknown_real_explained_as_known_sl_artifact_allows_submission_and_reconciles_position(
    tmp_path: Path,
) -> None:
    """Reconstructs run #4's real incident shape (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md):
    ticket 171909600 is MT5's own SL-execution order closing locally-known position 171908077.
    Fully explained -> posture becomes NORMAL despite a raw unknown_real, an UNRELATED submit()
    succeeds, the closed position is auto-reconciled to CLOSED, and the artifact ticket itself
    is never adopted as its own local record."""
    store = StateStore(tmp_path / "order_state")
    _seed_grid_position(store, 171908077, side="SELL", requested_sl=65087.71, requested_tp=64500.0)
    deals_csv = (
        _SL_ARTIFACT_DEALS_CSV_HEADER
        + "2026-08-10 02:43:27,88000001,171909600,0,0,1,0,171908077,0,0.01,65087.71,0.0,0.0,-0.6,0.0,"
          "BTCUSD,[sl 65087.71],\n"
    )
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON, "get_deals": deals_csv})
    account = _mock_account(orders=[
        OrderState(ticket=171909600, symbol="BTCUSD", side="BUY", volume=0.01, price=65087.71, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))  # unrelated LIMIT order

    assert result.success is True  # posture became NORMAL -- 171909600 was fully explained
    assert store.lookup(171908077).status == "CLOSED"  # type: ignore[union-attr]
    assert store.lookup(171909600) is None  # never adopted -- no record for the artifact itself
    assert [name for name, _ in client.calls][0] == "get_deals"  # evidence gathered before submit


def test_unknown_real_explained_as_known_tp_artifact_allows_submission_and_reconciles_position(
    tmp_path: Path,
) -> None:
    """Symmetric TP-close case (no live TP incident has actually been observed yet -- see
    test_state_sl_tp_artifact.py's module docstring -- but the wiring is identical either way)."""
    store = StateStore(tmp_path / "order_state")
    _seed_grid_position(store, 171930000, side="BUY", requested_sl=64000.0, requested_tp=66000.0)
    deals_csv = (
        _SL_ARTIFACT_DEALS_CSV_HEADER
        + "2026-08-10 02:43:27,88000002,171930999,0,1,1,0,171930000,0,0.01,66000.0,0.0,0.0,8.29,0.0,"
          "BTCUSD,[tp 66000.0],\n"
    )
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON, "get_deals": deals_csv})
    account = _mock_account(orders=[
        OrderState(ticket=171930999, symbol="BTCUSD", side="SELL", volume=0.01, price=66000.0, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    result = asyncio.run(executor.submit(_order_plan()))

    assert result.success is True
    assert store.lookup(171930000).status == "CLOSED"  # type: ignore[union-attr]
    assert store.lookup(171930999) is None


def test_unknown_real_with_real_but_insufficient_deal_evidence_still_blocks(tmp_path: Path) -> None:
    """get_deals() succeeds and returns real data, but it doesn't satisfy every evidence
    requirement (here: wrong closing side for the position it claims to close) -- must still
    trip MANAGE_ONLY exactly as if no explanation had ever been attempted. Distinguishes "no
    evidence" (already covered by the EMPTY_DEALS_CSV tests above) from "evidence present but
    not strong enough"."""
    store = StateStore(tmp_path / "order_state")
    _seed_grid_position(store, 171908077, side="SELL", requested_sl=65087.71, requested_tp=64500.0)
    # type=1 (SELL) closing a SELL position is never valid -- must be a BUY(0) deal.
    deals_csv = (
        _SL_ARTIFACT_DEALS_CSV_HEADER
        + "2026-08-10 02:43:27,88000001,171909600,0,1,1,0,171908077,0,0.01,65087.71,0.0,0.0,-0.6,0.0,"
          "BTCUSD,[sl 65087.71],\n"
    )
    client = _StubMcpClient({"place_pending_order": SUCCESS_PLACE_JSON, "get_deals": deals_csv})
    account = _mock_account(orders=[
        OrderState(ticket=171909600, symbol="BTCUSD", side="BUY", volume=0.01, price=65087.71, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.submit(_order_plan()))
    assert store.lookup(171908077).status == "OPEN"  # type: ignore[union-attr]  -- untouched
    assert not any(name == "place_pending_order" for name, _ in client.calls)


def test_get_deals_failure_during_explanation_fails_closed_and_still_blocks(tmp_path: Path) -> None:
    """A raised exception while gathering evidence (dropped connection, etc.) must be exactly as
    safe as no evidence at all -- MANAGE_ONLY still trips, nothing is ever assumed explained on
    a failure."""
    store = StateStore(tmp_path / "order_state")
    _seed_grid_position(store, 171908077, side="SELL", requested_sl=65087.71, requested_tp=64500.0)
    client = _StubMcpClient(
        {"place_pending_order": SUCCESS_PLACE_JSON}, fail=frozenset({"get_deals"}),
    )
    account = _mock_account(orders=[
        OrderState(ticket=171909600, symbol="BTCUSD", side="BUY", volume=0.01, price=65087.71, magic=0),
    ])
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    with pytest.raises(ExecutionBlockedError):
        asyncio.run(executor.submit(_order_plan()))
    assert store.lookup(171908077).status == "OPEN"  # type: ignore[union-attr]  -- untouched
    assert not any(name == "place_pending_order" for name, _ in client.calls)


def test_explained_artifact_position_reconciliation_is_idempotent(tmp_path: Path) -> None:
    """StateStore.record_closed() -- what _explain_unknown_real() calls for an explained
    artifact's underlying position -- must be safe to call more than once for the same ticket
    without erroring or corrupting the record (guards against ever seeing the same explainable
    evidence reconciled twice, e.g. across two posture checks in the same still-live window).
    Once a position is reconciled CLOSED it also drops out of local_open, so a later posture
    check naturally can no longer re-explain further evidence against it -- see
    test_state_sl_tp_artifact.py's own idempotency test for classify_unknown_real_tickets()
    itself, which this complements at the StateStore-write level actually exercised here."""
    store = StateStore(tmp_path / "order_state")
    _seed_grid_position(store, 171908077, side="SELL", requested_sl=65087.71, requested_tp=64500.0)
    closed_at = datetime(2026, 8, 10, 2, 43, 27, tzinfo=timezone.utc)

    store.record_closed(171908077, reason="auto-reconciled: SL close confirmed", closed_at=closed_at)
    store.record_closed(171908077, reason="auto-reconciled: SL close confirmed", closed_at=closed_at)

    record = store.lookup(171908077)
    assert record is not None
    assert record.status == "CLOSED"


# ---------- ToolRegistry authorization enforcement ----------

def test_registry_classification_end_to_end(tmp_path: Path) -> None:
    """One stub client shared by a real McpAccountReader and McpOrderExecutor -- proves every
    tool call across both is correctly classified (TRADING for mutations, READ_ONLY for
    verification/account reads), the same way it would live."""
    store = StateStore(tmp_path / "order_state")
    client = _StubMcpClient({
        "get_account_info": _account_json(),
        "get_positions_with_magic": EMPTY_POSITIONS_CSV,
        "get_pending_orders_with_magic": EMPTY_ORDERS_CSV,
        "place_pending_order": SUCCESS_PLACE_JSON,
        "cancel_pending_order": SUCCESS_CANCEL_JSON,
        "close_position": SUCCESS_CLOSE_JSON,
    })
    account = McpAccountReader(client)
    executor = McpOrderExecutor(client, account, store, mt5_account_kind="DEMO")

    asyncio.run(executor.submit(_order_plan()))
    asyncio.run(executor.cancel(123456))
    asyncio.run(executor.close_position(555555))

    assert len(client.calls) >= 5
    mutating = {"place_pending_order", "cancel_pending_order", "close_position"}
    for name, _ in client.calls:
        expected = ToolClass.TRADING if name in mutating else ToolClass.READ_ONLY
        assert client.registry.classification_of(name) == expected, name
