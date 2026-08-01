from __future__ import annotations

import pytest

from mt5_mcp_trading.domain.models import RiskDecision
from mt5_mcp_trading.risk.combine import combine


def test_all_approved_combines_to_approved() -> None:
    result = combine([
        RiskDecision(approved=True, reasons=("a ok",)),
        RiskDecision(approved=True, reasons=("b ok",)),
    ])
    assert result.approved is True
    assert result.reasons == ("a ok", "b ok")
    assert result.blocking_guard is None


def test_any_rejection_combines_to_rejected() -> None:
    result = combine([
        RiskDecision(approved=True, reasons=("a ok",)),
        RiskDecision(approved=False, reasons=("b blocked",), blocking_guard="symbol.duplicate_order"),
    ])
    assert result.approved is False
    assert result.blocking_guard == "symbol.duplicate_order"
    assert result.reasons == ("a ok", "b blocked")  # every guard's reasons carried through


def test_first_rejection_wins_blocking_guard_when_multiple_reject() -> None:
    result = combine([
        RiskDecision(approved=False, reasons=("first",), blocking_guard="portfolio.max_open_lots"),
        RiskDecision(approved=False, reasons=("second",), blocking_guard="symbol.duplicate_order"),
    ])
    assert result.blocking_guard == "portfolio.max_open_lots"


def test_single_decision_passes_through() -> None:
    only = RiskDecision(approved=True, reasons=("solo",))
    assert combine([only]) == RiskDecision(approved=True, reasons=("solo",))


def test_empty_sequence_raises() -> None:
    with pytest.raises(ValueError):
        combine([])
