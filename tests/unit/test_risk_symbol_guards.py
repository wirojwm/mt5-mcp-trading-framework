from __future__ import annotations

from mt5_mcp_trading.domain.models import OrderState
from mt5_mcp_trading.risk.symbol_guards import check_duplicate_order

POINT = 0.01


def _order(price: float, side: str = "BUY", ticket: int = 1) -> OrderState:
    return OrderState(ticket=ticket, symbol="BTCUSD", side=side, volume=0.01, price=price, magic=71101)


def test_approved_when_no_existing_orders() -> None:
    result = check_duplicate_order([], "BUY", price=100.0, point=POINT)
    assert result.approved is True


def test_rejected_when_within_tolerance_of_existing_same_side_order() -> None:
    existing = [_order(100.00)]
    # tol = 5 ticks * 0.01 = 0.05; proposed 100.03 is within that of 100.00
    result = check_duplicate_order(existing, "BUY", price=100.03, point=POINT, tol_ticks=5)
    assert result.approved is False
    assert result.blocking_guard == "symbol.duplicate_order"


def test_approved_when_just_outside_tolerance() -> None:
    existing = [_order(100.00)]
    # tol = 0.05; 100.06 is just outside
    result = check_duplicate_order(existing, "BUY", price=100.06, point=POINT, tol_ticks=5)
    assert result.approved is True


def test_exactly_at_tolerance_boundary_counts_as_duplicate() -> None:
    # Legacy uses <=, not <.
    existing = [_order(100.00)]
    result = check_duplicate_order(existing, "BUY", price=100.05, point=POINT, tol_ticks=5)
    assert result.approved is False


def test_opposite_side_at_same_price_is_not_a_duplicate() -> None:
    existing = [_order(100.00, side="SELL")]
    result = check_duplicate_order(existing, "BUY", price=100.00, point=POINT)
    assert result.approved is True


def test_far_away_same_side_order_is_not_a_duplicate() -> None:
    existing = [_order(50.00)]
    result = check_duplicate_order(existing, "BUY", price=100.00, point=POINT)
    assert result.approved is True


def test_custom_tol_ticks_widens_or_narrows_the_window() -> None:
    existing = [_order(100.00)]
    narrow = check_duplicate_order(existing, "BUY", price=100.03, point=POINT, tol_ticks=1)
    wide = check_duplicate_order(existing, "BUY", price=100.03, point=POINT, tol_ticks=10)
    assert narrow.approved is True   # tol=0.01, 0.03 away is outside
    assert wide.approved is False    # tol=0.10, 0.03 away is inside
