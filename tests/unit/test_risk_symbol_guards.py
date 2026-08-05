from __future__ import annotations

from mt5_mcp_trading.domain.models import OrderState, PositionState
from mt5_mcp_trading.risk.symbol_guards import check_duplicate_order, check_position_limit

POINT = 0.01


def _order(price: float, side: str = "BUY", ticket: int = 1) -> OrderState:
    return OrderState(ticket=ticket, symbol="BTCUSD", side=side, volume=0.01, price=price, magic=71101)


def _position(ticket: int = 1, side: str = "BUY") -> PositionState:
    return PositionState(ticket=ticket, symbol="BTCUSD", side=side, volume=0.01, price_open=100.0,
                          profit=0.0, magic=72101)


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


# ---------- check_position_limit (runner re-entry throttle, docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md) ----------

def test_position_limit_approved_when_no_open_positions() -> None:
    result = check_position_limit([], max_concurrent=1)
    assert result.approved is True


def test_position_limit_rejects_at_the_limit() -> None:
    result = check_position_limit([_position()], max_concurrent=1)
    assert result.approved is False
    assert result.blocking_guard == "symbol.position_limit"


def test_position_limit_approved_below_a_higher_limit() -> None:
    result = check_position_limit([_position()], max_concurrent=2)
    assert result.approved is True


def test_position_limit_rejects_when_over_the_limit() -> None:
    result = check_position_limit([_position(1), _position(2), _position(3)], max_concurrent=2)
    assert result.approved is False
