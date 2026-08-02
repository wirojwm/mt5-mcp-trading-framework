"""
reconcile(): pure ticket-only cross-check, no I/O, no live call possible even in principle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from mt5_mcp_trading.domain.models import OrderState, PositionState
from mt5_mcp_trading.state.models import LocalOrderRecord
from mt5_mcp_trading.state.reconcile import reconcile


def _local(ticket: int, symbol: str = "BTCUSD") -> LocalOrderRecord:
    return LocalOrderRecord(
        ticket=ticket, strategy="grid", magic=71101, comment="grid_buy", symbol=symbol,
        side="BUY", order_type="LIMIT", requested_volume=0.01, requested_price=63000.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode="FOK", requested_expiry=None, retcode=10009,
        executed_price=63000.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=datetime(2026, 8, 2, tzinfo=timezone.utc), closed_at=None,
        status="OPEN", closed_reason=None,
    )


def _position(ticket: int, symbol: str = "BTCUSD") -> PositionState:
    return PositionState(
        ticket=ticket, symbol=symbol, side="BUY", volume=0.01, price_open=63000.0,
        profit=0.0, magic=0,  # MT5-side magic is always 0 -- see docs known issue 7
    )


def _order(ticket: int, symbol: str = "BTCUSD") -> OrderState:
    return OrderState(ticket=ticket, symbol=symbol, side="BUY", volume=0.01, price=63000.0, magic=0)


def test_empty_everything_reconciles_to_empty() -> None:
    report = reconcile([], [], [])
    assert report.matched == ()
    assert report.local_only == ()
    assert report.unknown_real == ()


def test_matched_ticket_present_in_both() -> None:
    report = reconcile([_local(1)], [_position(1)], [])
    assert report.matched == (1,)
    assert report.local_only == ()
    assert report.unknown_real == ()


def test_local_only_when_locally_open_but_absent_from_real_snapshot() -> None:
    report = reconcile([_local(1)], [], [])
    assert report.matched == ()
    assert report.local_only == (1,)
    assert report.unknown_real == ()


def test_unknown_real_when_real_ticket_has_no_local_record() -> None:
    report = reconcile([], [_position(1)], [])
    assert report.matched == ()
    assert report.local_only == ()
    assert report.unknown_real == (1,)


def test_combined_case_across_positions_and_orders() -> None:
    local = [_local(1), _local(2)]
    positions = [_position(1), _position(3)]  # 1 matched, 3 unknown
    orders = [_order(2), _order(4)]  # 2 matched, 4 unknown
    report = reconcile(local, positions, orders)
    assert report.matched == (1, 2)
    assert report.local_only == ()
    assert set(report.unknown_real) == {3, 4}


def test_matching_is_by_ticket_only_never_symbol() -> None:
    # Same ticket, deliberately different symbol between the local record and the real
    # position -- must still match by ticket alone. This documents the intentional behavior:
    # symbol is never part of the ownership decision.
    report = reconcile([_local(1, symbol="BTCUSD")], [_position(1, symbol="XAUUSD")], [])
    assert report.matched == (1,)
    assert report.unknown_real == ()
