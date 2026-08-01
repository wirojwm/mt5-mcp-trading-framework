"""
LIMIT price normalization, ported from the legacy project's mt5_bridge.normalize_price():
snap to tick size, then push far enough from bid/ask to respect the broker's stops_level +
freeze_level, or give up and return None if that still doesn't hold.

    gap = (stops_level + freeze_level + 2) * point
    BUY_LIMIT:  must end up strictly below bid - gap
    SELL_LIMIT: must end up strictly above ask + gap

Only real change: takes SymbolInfo + Tick as plain data instead of calling
mt5_bridge.symbol_misc() (a live adapter call) internally -- this is exactly the kind of call
this project's architecture keeps out of anything upstream of execution/mt5_adapter. The
snap/adjust/re-check arithmetic itself is unchanged.

The None-returning path (adjustment still not sufficient) is unreachable under normal
arithmetic, not just untested: after the single 3-tick push, snap()'s worst-case rounding
error is at most half a tick, so the adjusted price is always at least 2.5 ticks past the
threshold it's being re-checked against. Confirmed both by probing with deliberately extreme
inputs (near-zero point, very large stops/freeze levels -- none triggered it) and by this
direct margin argument. Preserved as a defensive fallback regardless, matching the legacy
function.
"""

from __future__ import annotations

from typing import Literal, Optional

from mt5_mcp_trading.domain.models import SymbolInfo, Tick

LimitSide = Literal["BUY_LIMIT", "SELL_LIMIT"]


def normalize_limit_price(
    price: float,
    side: LimitSide,
    symbol_info: SymbolInfo,
    tick: Tick,
) -> Optional[float]:
    point = symbol_info.point
    gap = (symbol_info.stops_level + symbol_info.freeze_level + 2) * point

    def snap(x: float) -> float:
        return round(x / point) * point

    p = snap(price)

    if side == "BUY_LIMIT":
        min_ok = tick.bid - gap
        if not (p < min_ok):
            p = snap(min_ok - 3 * point)
        if not (p < tick.bid - gap):
            return None
    else:  # SELL_LIMIT
        min_ok = tick.ask + gap
        if not (p > min_ok):
            p = snap(min_ok + 3 * point)
        if not (p > tick.ask + gap):
            return None

    return p
