"""
Grid strategy: EMA center + ATR-scaled step, one candidate level per side.

Ported from the legacy project's launcher_grid.py `_calc_prices()` + the level computation in
`_grid_worker` (`price_buy = center - step_price`, `price_sell = center + step_price`) -- the
currently-live implementation, not the archived bbn_grid.py/router_mt5.py engine, which Phase 0
found never worked and Phase 0/2 retired. "Preserve the intended behavior" here means preserving
*this* implementation's behavior specifically.

Two deliberate deviations from the legacy function, both were architecture problems, not
behavior worth preserving:

1. `_calc_prices` fell back to a live bid/ask mid-price when given an empty DataFrame -- pulling
   in a live-tick adapter concern in the middle of what should be pure math. This function
   requires at least one bar and raises otherwise; supplying a synthetic/last-known bar when
   real bars aren't available is the caller's problem, not this one's.
2. The legacy function converted price deltas to "points" (dividing by the symbol's tick size)
   and back, purely to apply a `step_mult`/floor. Algebraically:
       step_pts = max(10.0, atr_pts * step_mult); step_price = step_pts * point
     is exactly
       step_price = max(10.0 * point, atr_price * step_mult)
   since atr_pts = atr_price / point. Implemented directly in price units below; the points
   detour added no behavior, only an extra unit conversion. See tests/unit/test_strategy_grid.py
   for the numeric proof this still matches the legacy formula's outputs.

GridLevels (domain.models) is deliberately not a Signal -- see that type's docstring. How grid
output becomes an actual TradeIntent (bias-aware seeding, one-per-side placement, dedup against
already-live pending orders -- all real behavior in the legacy launcher_grid._grid_worker) is
not decided by this function and is out of scope for this migration step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from mt5_mcp_trading.domain.models import GridLevels, MarketBar
from mt5_mcp_trading.features.atr import atr as compute_atr
from mt5_mcp_trading.features.ema import ema as compute_ema

CenterMode = Literal["ema", "last"]


@dataclass(frozen=True, slots=True)
class GridStrategyConfig:
    atr_period: int = 14
    center_ema_period: int = 50
    center_mode: CenterMode = "ema"
    step_mult: float = 0.4
    min_step_points: float = 10.0  # legacy: "at least 10 ticks"
    # NOTE: legacy always derives tp from step_mult dynamically (`step_mult * 1.2`), not from
    # an independent value -- deliberately NOT a separate config field here, so overriding
    # step_mult can never silently leave tp_mult stale/inconsistent. See compute_grid_levels.
    # sl_atr_mult has NO legacy precedent -- launcher_grid.py never attached a stop-loss to its
    # pending orders at all (this module's docstring says nothing about legacy SL/TP behavior
    # either way, unlike strategy/runner.py, which explicitly confirms its legacy had none).
    # New, project-original design, deliberately independent of step_mult/tp's formula so this
    # can never make the existing, legacy-matched tp_price computation stale.
    sl_atr_mult: float = 2.0


def compute_grid_levels(bars: Sequence[MarketBar], point: float, config: GridStrategyConfig) -> GridLevels:
    """
    bars: at least one bar, most recent last. point: the symbol's tick size (SymbolInfo.point).
    Raises ValueError if bars is empty -- see module docstring, deviation 1.
    """
    if not bars:
        raise ValueError("compute_grid_levels requires at least one bar")

    symbol = bars[-1].symbol
    timestamp = bars[-1].time
    closes = [b.close for b in bars]

    atr_value = compute_atr(bars, config.atr_period)

    if atr_value <= 0:
        # Legacy fallback: center = last close, step = tp = exactly min_step_points * point
        # (not step_mult/tp_mult-scaled -- that asymmetry is in the original code too).
        # sl_price mirrors the same fallback shape -- no legacy behavior to preserve here, but
        # consistent with tp_price/step_price's existing floor convention.
        center = closes[-1]
        step_price = config.min_step_points * point
        tp_price = config.min_step_points * point
        sl_price = config.min_step_points * point
        atr_value = 0.0
    else:
        center = (
            compute_ema(closes, config.center_ema_period)
            if config.center_mode == "ema"
            else closes[-1]
        )
        step_price = max(config.min_step_points * point, atr_value * config.step_mult)
        tp_price = max(config.min_step_points * point, atr_value * config.step_mult * 1.2)
        sl_price = max(config.min_step_points * point, atr_value * config.sl_atr_mult)

    return GridLevels(
        symbol=symbol,
        timestamp=timestamp,
        center=center,
        atr=atr_value,
        step_price=step_price,
        tp_price=tp_price,
        sl_price=sl_price,
        buy_price=center - step_price,
        sell_price=center + step_price,
    )
