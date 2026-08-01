"""
Expected values cross-checked against the legacy formulas (money_management.py's decide_lot
branches) in a standalone scratch script -- not derived from this implementation itself or
from importing the legacy code.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import TradeIntent
from mt5_mcp_trading.sizing.money import MoneyConfig, decide_lot, to_sized_intent


def test_fixed_mode_returns_fixed_lot() -> None:
    config = MoneyConfig(lot_size_mode="fixed", fixed_lot=0.03)
    result = decide_lot(config)
    assert result.lot == 0.03
    assert result.mode == "fixed"
    assert "fixed_lot=0.03" in result.reasons[0]


def test_atr_scale_unclamped_matches_legacy_formula() -> None:
    config = MoneyConfig(lot_size_mode="atr_scale", atr_scale_base=0.01, atr_scale_ref=1.0,
                          atr_scale_min=0.01, atr_scale_max=0.10)
    result = decide_lot(config, step=0.5)
    assert result.lot == pytest.approx(0.02, abs=1e-12)


def test_atr_scale_clamps_to_max_when_step_tiny() -> None:
    config = MoneyConfig(lot_size_mode="atr_scale", atr_scale_min=0.01, atr_scale_max=0.10)
    result = decide_lot(config, step=0.001)
    assert result.lot == pytest.approx(0.10, abs=1e-12)


def test_atr_scale_clamps_to_min_when_step_huge() -> None:
    config = MoneyConfig(lot_size_mode="atr_scale", atr_scale_min=0.01, atr_scale_max=0.10)
    result = decide_lot(config, step=1000.0)
    assert result.lot == pytest.approx(0.01, abs=1e-12)


def test_atr_scale_prefers_step_over_atr_when_both_given() -> None:
    config = MoneyConfig(lot_size_mode="atr_scale")
    with_step = decide_lot(config, step=0.5, atr=999.0)
    without_step = decide_lot(config, step=0.5)
    assert with_step.lot == without_step.lot  # atr=999 must be ignored when step is present


def test_atr_scale_falls_back_to_atr_when_step_missing() -> None:
    config = MoneyConfig(lot_size_mode="atr_scale")
    via_atr = decide_lot(config, atr=0.5)
    via_step = decide_lot(config, step=0.5)
    assert via_atr.lot == via_step.lot


def test_risk_percent_unclamped_matches_legacy_formula() -> None:
    config = MoneyConfig(lot_size_mode="risk_percent", risk_per_trade=0.001,
                          stop_distance_points=200.0, atr_scale_min=0.01, atr_scale_max=0.10)
    result = decide_lot(config, account_balance=10000.0, point_value_per_lot=1.0, price_point=0.01)
    assert result.lot == pytest.approx(0.05, abs=1e-12)


def test_risk_percent_falls_back_to_fixed_when_point_value_per_lot_missing() -> None:
    # Regression test for the crash bug found & fixed during migration: passing no
    # point_value_per_lot (very common -- launcher_grid.GridConfig has no such attribute at
    # all) must fall back to fixed_lot, not raise.
    config = MoneyConfig(lot_size_mode="risk_percent", risk_per_trade=0.01,
                          stop_distance_points=200.0, fixed_lot=0.02)
    result = decide_lot(config, account_balance=10000.0, point_value_per_lot=None)
    assert result.lot == 0.02
    assert "insufficient inputs" in result.reasons[0]


def test_risk_percent_falls_back_to_fixed_when_price_point_missing() -> None:
    # price_point is never used in the risk formula itself (only balance/stop/point_value_
    # per_lot are) -- in the legacy code it exists purely as an insufficient-inputs gate.
    # Omitting it must still trigger the fallback, confirming that gate is preserved even
    # though the value itself is otherwise inert.
    config = MoneyConfig(lot_size_mode="risk_percent", risk_per_trade=0.01,
                          stop_distance_points=200.0, fixed_lot=0.02)
    result = decide_lot(config, account_balance=10000.0, point_value_per_lot=1.0, price_point=None)
    assert result.lot == 0.02
    assert "insufficient inputs" in result.reasons[0]


def test_risk_percent_falls_back_when_stop_distance_points_missing() -> None:
    config = MoneyConfig(lot_size_mode="risk_percent", risk_per_trade=0.01, fixed_lot=0.02)
    result = decide_lot(config, account_balance=10000.0, point_value_per_lot=1.0)
    assert result.lot == 0.02
    assert "insufficient inputs" in result.reasons[0]


def test_risk_percent_falls_back_when_risk_per_trade_is_zero() -> None:
    config = MoneyConfig(lot_size_mode="risk_percent", risk_per_trade=0.0,
                          stop_distance_points=200.0, fixed_lot=0.02)
    result = decide_lot(config, account_balance=10000.0, point_value_per_lot=1.0)
    assert result.lot == 0.02


def test_unknown_mode_falls_back_to_fixed() -> None:
    config = MoneyConfig(lot_size_mode="bogus_mode", fixed_lot=0.04)
    result = decide_lot(config)
    assert result.lot == 0.04
    assert "not recognized" in result.reasons[0]


def test_mode_is_case_and_whitespace_insensitive() -> None:
    config = MoneyConfig(lot_size_mode="  ATR_Scale  ")
    result = decide_lot(config, step=0.5)
    assert result.mode == "atr_scale"


def test_max_open_lots_hint_adds_an_informational_reason_when_set() -> None:
    config = MoneyConfig(lot_size_mode="fixed", max_open_lots_hint=0.06)
    result = decide_lot(config, open_lots_now=0.02, pending_lots_now=0.01)
    assert any("hint:max_open_lots=0.06" in r for r in result.reasons)
    assert any("now_open=0.02" in r and "now_pending=0.01" in r for r in result.reasons)


def test_no_hint_reason_when_max_open_lots_hint_not_set() -> None:
    config = MoneyConfig(lot_size_mode="fixed")
    result = decide_lot(config)
    assert not any("hint:" in r for r in result.reasons)


def test_account_balance_defaults_to_zero_not_a_live_adapter_call() -> None:
    # No adapter fallback exists anymore -- omitting account_balance must behave exactly like
    # passing 0.0, never attempt any I/O.
    config = MoneyConfig(lot_size_mode="risk_percent", risk_per_trade=0.5,
                          stop_distance_points=200.0, atr_scale_min=0.01, atr_scale_max=0.10)
    result = decide_lot(config, point_value_per_lot=1.0, price_point=0.01)  # account_balance omitted
    assert "insufficient inputs" not in result.reasons[0]  # confirms it took the formula path, not fallback
    assert result.lot == pytest.approx(0.01, abs=1e-12)  # risk_dollar=0 -> lot_raw=0 -> clamped to min


def _intent() -> TradeIntent:
    return TradeIntent(
        symbol="BTCUSD", side="BUY", strategy_name="grid", desired_order_type="LIMIT",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), reference_price=63000.0,
    )


def test_to_sized_intent_carries_lot_mode_and_joined_reasons() -> None:
    config = MoneyConfig(lot_size_mode="fixed", fixed_lot=0.02)
    decision = decide_lot(config)
    sized = to_sized_intent(_intent(), decision)

    assert sized.intent is not None
    assert sized.intent.symbol == "BTCUSD"
    assert sized.volume == 0.02
    assert sized.sizing_mode == "fixed"
    assert sized.sizing_rationale == "; ".join(decision.reasons)


def test_to_sized_intent_preserves_the_original_intent_object() -> None:
    intent = _intent()
    decision = decide_lot(MoneyConfig(lot_size_mode="fixed"))
    sized = to_sized_intent(intent, decision)
    assert sized.intent is intent
