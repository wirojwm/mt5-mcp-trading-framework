from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import (
    RiskDecision,
    Signal,
    SizedIntent,
    SymbolInfo,
    Tick,
    TradeIntent,
)
from mt5_mcp_trading.order_planning.plan import build_order_plan

SYMBOL_INFO = SymbolInfo(
    symbol="BTCUSD", digits=2, point=0.01, volume_min=0.01, volume_max=1.0,
    volume_step=0.01, stops_level=10, freeze_level=5,
)
TICK = Tick(symbol="BTCUSD", bid=100.00, ask=100.02, time=datetime(2026, 1, 1, tzinfo=timezone.utc))
APPROVED = RiskDecision(approved=True, reasons=("ok",))
REJECTED = RiskDecision(approved=False, reasons=("no",), blocking_guard="test.block")


def _sized_intent(
    side: str = "BUY",
    order_type: str = "LIMIT",
    reference_price: float | None = 99.90,
    volume: float = 0.013,
) -> SizedIntent:
    signal = Signal(symbol="BTCUSD", strategy_name="grid", direction="LONG",
                     timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    intent = TradeIntent(
        symbol="BTCUSD", side=side, strategy_name="grid", signal_ref=signal,
        desired_order_type=order_type, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        reference_price=reference_price,
    )
    return SizedIntent(intent=intent, volume=volume, sizing_mode="fixed")


def test_raises_when_risk_decision_not_approved() -> None:
    with pytest.raises(ValueError):
        build_order_plan(_sized_intent(), REJECTED, SYMBOL_INFO, TICK, magic=71101)


def test_limit_order_uses_normalized_price() -> None:
    plan = build_order_plan(_sized_intent(reference_price=99.90), APPROVED, SYMBOL_INFO, TICK, magic=71101)
    assert plan is not None
    assert plan.order_type == "LIMIT"
    assert plan.side == "BUY"
    assert plan.price == pytest.approx(99.8, abs=1e-9)  # same scratch-verified value as limit_price tests


def test_limit_order_without_reference_price_raises() -> None:
    # Distinct from normalize_limit_price returning None (mathematically unreachable under
    # normal snap arithmetic, per limit_price.py's docstring) -- this is the other, actually
    # reachable LIMIT failure mode: no reference_price to normalize at all.
    intent_without_price = _sized_intent(reference_price=None)
    with pytest.raises(ValueError):
        build_order_plan(intent_without_price, APPROVED, SYMBOL_INFO, TICK, magic=71101)


def test_market_order_uses_tick_price_when_no_reference_price() -> None:
    plan = build_order_plan(
        _sized_intent(side="BUY", order_type="MARKET", reference_price=None),
        APPROVED, SYMBOL_INFO, TICK, magic=71101,
    )
    assert plan is not None
    assert plan.price == TICK.ask  # BUY market order fills at ask


def test_market_sell_uses_bid() -> None:
    plan = build_order_plan(
        _sized_intent(side="SELL", order_type="MARKET", reference_price=None),
        APPROVED, SYMBOL_INFO, TICK, magic=71101,
    )
    assert plan is not None
    assert plan.price == TICK.bid


def test_market_order_respects_explicit_reference_price_over_tick() -> None:
    plan = build_order_plan(
        _sized_intent(side="BUY", order_type="MARKET", reference_price=101.23),
        APPROVED, SYMBOL_INFO, TICK, magic=71101,
    )
    assert plan is not None
    assert plan.price == 101.23


def test_volume_is_rounded_to_volume_step() -> None:
    # 0.013 rounded to the nearest 0.01 step -> 0.01
    plan = build_order_plan(_sized_intent(volume=0.013), APPROVED, SYMBOL_INFO, TICK, magic=71101)
    assert plan is not None
    assert plan.volume == pytest.approx(0.01, abs=1e-9)


def test_volume_is_clamped_to_volume_max() -> None:
    plan = build_order_plan(_sized_intent(volume=5.0), APPROVED, SYMBOL_INFO, TICK, magic=71101)
    assert plan is not None
    assert plan.volume == pytest.approx(SYMBOL_INFO.volume_max, abs=1e-9)


def test_volume_is_clamped_to_volume_min() -> None:
    plan = build_order_plan(_sized_intent(volume=0.001), APPROVED, SYMBOL_INFO, TICK, magic=71101)
    assert plan is not None
    assert plan.volume == pytest.approx(SYMBOL_INFO.volume_min, abs=1e-9)


def test_comment_defaults_to_strategy_name_when_not_given() -> None:
    plan = build_order_plan(_sized_intent(), APPROVED, SYMBOL_INFO, TICK, magic=71101)
    assert plan is not None
    assert plan.comment == "grid"


def test_comment_override_is_used_when_given() -> None:
    plan = build_order_plan(_sized_intent(), APPROVED, SYMBOL_INFO, TICK, magic=71101, comment="grid_buy_1")
    assert plan is not None
    assert plan.comment == "grid_buy_1"


def test_magic_sl_tp_deviation_pass_through() -> None:
    plan = build_order_plan(
        _sized_intent(), APPROVED, SYMBOL_INFO, TICK, magic=71101, sl=1.0, tp=2.0, deviation=200,
    )
    assert plan is not None
    assert plan.magic == 71101
    assert plan.sl == 1.0
    assert plan.tp == 2.0
    assert plan.deviation == 200
