"""
Assembles a SizedIntent (+ an already-approved RiskDecision) into a final OrderPlan.

Not a migration in the Phase 4 sense -- there is no single legacy function this corresponds
to. It exists to fulfil what the sizing migration's docstring promised: broker-constraint
normalization (price via limit_price.py, volume via _clamp_volume below) belongs here, not in
sizing/money.py.

One piece is genuinely new, not ported, and that distinction matters: money_management.py's
_normalize_volume() called `mt5_bridge.normalize_volume(symbol, vol)` if that function
existed on the bridge -- but mt5_bridge.py never actually defines a `normalize_volume`
function. `hasattr(bridge, "normalize_volume")` was always False, so the legacy volume-
clamping hook was always a no-op in every real code path. _clamp_volume() below is therefore
new functionality (round to volume_step, clamp to [volume_min, volume_max]) built from
SymbolInfo's existing fields, not a port of working legacy behavior -- there was none to port.

RiskDecision is required and checked defensively: build_order_plan() raises if
risk_decision.approved is False, so "no OrderPlan may be built from a rejected intent" (the
architecture's own rule) is enforced structurally here, not just by caller discipline.
"""

from __future__ import annotations

from typing import Optional

from mt5_mcp_trading.domain.models import OrderPlan, RiskDecision, SizedIntent, SymbolInfo, Tick
from mt5_mcp_trading.order_planning.limit_price import normalize_limit_price


def _clamp_volume(volume: float, symbol_info: SymbolInfo) -> float:
    step = symbol_info.volume_step
    if step > 0:
        volume = round(volume / step) * step
    volume = max(symbol_info.volume_min, min(symbol_info.volume_max, volume))
    return round(volume, 8)  # guard against float noise from the division/round above


def build_order_plan(
    sized_intent: SizedIntent,
    risk_decision: RiskDecision,
    symbol_info: SymbolInfo,
    tick: Tick,
    magic: int,
    comment: Optional[str] = None,
    deviation: int = 150,
    sl: float = 0.0,
    tp: float = 0.0,
) -> Optional[OrderPlan]:
    """Returns None if the intent is a LIMIT order whose price can't be normalized far enough
    from the market (see limit_price.normalize_limit_price)."""
    if not risk_decision.approved:
        raise ValueError(
            "build_order_plan called with a non-approved RiskDecision; no OrderPlan may be "
            "built for a rejected intent"
        )

    intent = sized_intent.intent

    if intent.desired_order_type == "LIMIT":
        if intent.reference_price is None:
            raise ValueError("LIMIT order requires TradeIntent.reference_price")
        limit_side = "BUY_LIMIT" if intent.side == "BUY" else "SELL_LIMIT"
        price = normalize_limit_price(intent.reference_price, limit_side, symbol_info, tick)
        if price is None:
            return None
    else:
        price = (
            intent.reference_price
            if intent.reference_price is not None
            else (tick.ask if intent.side == "BUY" else tick.bid)
        )

    return OrderPlan(
        symbol=intent.symbol,
        order_type=intent.desired_order_type,
        side=intent.side,
        volume=_clamp_volume(sized_intent.volume, symbol_info),
        price=price,
        sl=sl,
        tp=tp,
        deviation=deviation,
        magic=magic,
        comment=comment if comment is not None else intent.strategy_name,
    )
