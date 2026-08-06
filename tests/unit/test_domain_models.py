from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import (
    Deal,
    ExecutionResult,
    MarketBar,
    OrderPlan,
    RiskDecision,
    Signal,
    SizedIntent,
    Tick,
    TradeIntent,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_market_bar_is_frozen() -> None:
    bar = MarketBar(
        symbol="BTCUSD", timeframe="M1", time=_now(),
        open=1.0, high=2.0, low=0.5, close=1.5, tick_volume=10, spread=2,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        bar.close = 999.0  # type: ignore[misc]


def test_tick_is_frozen() -> None:
    tick = Tick(symbol="BTCUSD", bid=1.0, ask=1.1, time=_now())
    with pytest.raises(dataclasses.FrozenInstanceError):
        tick.bid = 2.0  # type: ignore[misc]


def test_trade_intent_carries_signal_reference() -> None:
    signal = Signal(
        symbol="BTCUSD", strategy_name="grid", direction="LONG", timestamp=_now(),
        rationale="ema center below price",
    )
    intent = TradeIntent(
        symbol="BTCUSD", side="BUY", strategy_name="grid", signal_ref=signal,
        desired_order_type="LIMIT", timestamp=_now(), reference_price=63000.0,
    )
    assert intent.signal_ref is signal
    assert intent.side == "BUY"


def test_sized_intent_wraps_intent_with_volume() -> None:
    signal = Signal(symbol="BTCUSD", strategy_name="grid", direction="LONG", timestamp=_now())
    intent = TradeIntent(
        symbol="BTCUSD", side="BUY", strategy_name="grid", signal_ref=signal,
        desired_order_type="MARKET", timestamp=_now(),
    )
    sized = SizedIntent(intent=intent, volume=0.01, sizing_mode="fixed")
    assert sized.intent is intent
    assert sized.volume == 0.01


def test_risk_decision_rejected_has_reasons() -> None:
    decision = RiskDecision(approved=False, reasons=("spread too wide",), blocking_guard="symbol.spread_filter")
    assert decision.approved is False
    assert "spread too wide" in decision.reasons
    assert decision.blocking_guard == "symbol.spread_filter"


def test_order_plan_and_execution_result_round_trip() -> None:
    plan = OrderPlan(
        symbol="BTCUSD", order_type="LIMIT", side="BUY", volume=0.01, price=63000.0,
        sl=0.0, tp=0.0, deviation=150, magic=71101, comment="grid_buy_1",
    )
    result = ExecutionResult(
        order_plan=plan, success=True, retcode=10009, ticket=123, verified=True,
    )
    assert result.order_plan is plan
    assert result.verified is True


def test_execution_result_allows_no_order_plan_for_cancel_close() -> None:
    result = ExecutionResult(order_plan=None, success=True, retcode=10009, ticket=123)
    assert result.order_plan is None


def _deal(**overrides: object) -> Deal:
    # type=1 (DealType.SELL), entry=1 (out) -- see client_history.py's DealType(Enum).
    fields: dict[str, object] = dict(
        ticket=987654, order=171618036, position_id=171618036, time=_now(), type=1,
        entry=1, symbol="BTCUSD", volume=0.01, price=63500.0, profit=12.34, commission=-0.05,
        swap=0.0, fee=0.0, magic=0, comment="",
    )
    fields.update(overrides)
    return Deal(**fields)  # type: ignore[arg-type]


def test_deal_is_frozen() -> None:
    deal = _deal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        deal.profit = 999.0  # type: ignore[misc]


def test_deal_position_id_is_the_join_key_back_to_local_order_record_ticket() -> None:
    # Phase 9 Step 4 research: metatrader_client/order/close_position.py already treats the
    # ticket McpOrderExecutor.close_position(ticket) passes as a position_id -- so a real
    # ticket value (e.g. from LocalOrderRecord.ticket) is exactly what position_id holds.
    deal = _deal(position_id=171618036)
    assert deal.position_id == 171618036


def test_deal_carries_the_full_real_field_set_from_get_deals() -> None:
    # Confirmed by reading metatrader_client/client_history.py's own docstring (not guessed):
    # every field named there round-trips through construction unchanged.
    deal = _deal(
        ticket=1, order=2, position_id=3, entry=0, type=0, symbol="BTCUSD",  # type=0 (DealType.BUY)
        volume=0.02, price=63000.0, profit=0.0, commission=-0.06, swap=-1.2, fee=0.0,
        magic=71101, comment="grid_buy",
    )
    assert (deal.ticket, deal.order, deal.position_id, deal.entry, deal.type) == (1, 2, 3, 0, 0)
    assert (deal.commission, deal.swap, deal.fee) == (-0.06, -1.2, 0.0)
    assert deal.magic == 71101  # present on the type, even though callers must not trust it
    assert deal.comment == "grid_buy"
