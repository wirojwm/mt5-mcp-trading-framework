"""
Duplicate-order prevention, ported from the legacy project's launcher_grid._has_near_pending():

    tol = tol_ticks * point
    for o in pending_orders(symbol, magic):
        if o.type matches side and abs(o.price - price) <= tol:
            return True  # duplicate

Note on fidelity: the legacy check matches on the MT5 order *type* (BUY_LIMIT vs SELL_LIMIT
specifically, distinct from BUY_STOP/SELL_STOP). This project's OrderState domain type only
carries `side` ("BUY"/"SELL"), not the LIMIT/STOP distinction -- there was no reason to model
that finer distinction yet, since grid (the only strategy that places pending orders so far)
only ever uses LIMIT orders. check_duplicate_order() below matches on side only. If a
STOP-order-placing strategy is ever added, OrderState and this check will need to distinguish
order type, not just direction -- flagged here rather than silently assumed away.
"""

from __future__ import annotations

from typing import Sequence

from mt5_mcp_trading.domain.models import OrderState, PositionState, RiskDecision, Side


def check_duplicate_order(
    existing_orders: Sequence[OrderState],
    side: Side,
    price: float,
    point: float,
    tol_ticks: float = 5.0,
) -> RiskDecision:
    tolerance = tol_ticks * point

    for order in existing_orders:
        if order.side == side and abs(order.price - price) <= tolerance:
            return RiskDecision(
                approved=False,
                reasons=(
                    f"existing order {order.ticket} at {order.price} is within "
                    f"{tolerance} of proposed {side} price {price} (tol_ticks={tol_ticks})",
                ),
                blocking_guard="symbol.duplicate_order",
            )

    return RiskDecision(
        approved=True,
        reasons=(f"no existing {side} order within {tolerance} of proposed price {price}",),
    )


def check_position_limit(positions: Sequence[PositionState], max_concurrent: int) -> RiskDecision:
    """Caps the number of simultaneously OPEN positions for a magic -- distinct from
    check_duplicate_order() above (which only looks at PENDING orders sitting at a specific
    price) and portfolio_guards.check_exposure_cap() (which caps total lots, not position
    count). Not a legacy port -- new, added for runner specifically
    (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md, "Step 3"/"Fix runner's re-entry throttle"):
    run_runner_cycle() had no protection against re-entering the same direction repeatedly
    beyond the raw lot-based exposure cap, so every cycle where the signal stayed directional
    and any exposure "slot" was free, it opened another position -- a real, previously-invisible
    design gap only surfaced by backtesting at realistic scale (a 10,000-cycle real run against
    real BTCUSD M1 history produced 9,881 trades and -0.159R expectancy, traced to exactly this).

    `positions` must already be filtered to the relevant symbol/magic by the caller, matching
    every other guard in this module -- filtering is the caller's job, not this function's."""
    if len(positions) >= max_concurrent:
        return RiskDecision(
            approved=False,
            reasons=(
                f"{len(positions)} position(s) already open, at or above "
                f"max_concurrent={max_concurrent}",
            ),
            blocking_guard="symbol.position_limit",
        )
    return RiskDecision(
        approved=True,
        reasons=(f"{len(positions)} position(s) open, below max_concurrent={max_concurrent}",),
    )
