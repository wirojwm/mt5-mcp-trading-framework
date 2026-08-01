"""
Assembles a runner Signal into a TradeIntent, informed by the legacy project's
ema_crossover_core_multi.py reverse_to() -- the only concrete "act on a directional signal"
function that exists in the legacy codebase (confirmed: reverse_to()'s `side` parameter only
accepts +1/-1; FLAT/0 has no defined action anywhere -- the MONITOR-mode _loop() just records
a FLAT signal in state without acting on it). LONG -> BUY, SHORT -> SELL, both as MARKET
orders with no explicit price, matching reverse_to()'s own order_send() call (which also omits
a price and lets the broker fill at market). FLAT returns None: no TradeIntent at all, not a
zero-volume one or anything else -- there is nothing in the legacy code to preserve for that
case beyond "do nothing".

Deliberately narrower than reverse_to(), which is really two actions: close any existing
opposite-side position, THEN open a new one in the target direction. A TradeIntent represents
one intended new order, not a close-then-open sequence -- the "close opposite inventory
first" behavior is a position-management/execution-orchestration concern, out of scope here,
the same way grid's dedup-against-live-orders and the guard's freeze-wiring were deferred in
their own migrations.

signal_ref is populated with the actual Signal here (unlike trade_intent/grid.py, which
always leaves it None) -- this is exactly the case TradeIntent.signal_ref: Optional[Signal]
was designed for.
"""

from __future__ import annotations

from typing import Optional

from mt5_mcp_trading.domain.models import Signal, TradeIntent


def signal_to_trade_intent(signal: Signal) -> Optional[TradeIntent]:
    if signal.direction == "FLAT":
        return None

    side = "BUY" if signal.direction == "LONG" else "SELL"
    return TradeIntent(
        symbol=signal.symbol,
        side=side,
        strategy_name=signal.strategy_name,
        desired_order_type="MARKET",
        timestamp=signal.timestamp,
        reference_price=None,
        signal_ref=signal,
    )
