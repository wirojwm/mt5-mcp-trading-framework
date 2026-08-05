"""
Runner strategy: MACD-sign directional bias.

Ported from the legacy project's ema_crossover_core_multi.py `_loop()` decision logic:

    macd = _macd(closes, fast=fast, slow=slow)
    side = 1 if macd > 0 else (-1 if macd < 0 else 0)

-- mapped here to Signal.direction ("LONG"/"SHORT"/"FLAT"). Unlike the grid strategy, this one
maps naturally onto Signal (a single directional call), which is exactly the shape Signal was
designed around.

Bar-sufficiency: the legacy `_loop()` applies a *stricter* guard than `_macd()`'s own internal
one before even calling it: `if len(cl) < max(30, slow + 5): skip this cycle`. When skipped,
the legacy code leaves `_STATE[symbol]["last_side"]` untouched -- i.e. keeps whatever the
previous signal was. A pure function has no "previous value" to fall back to, so that
"insufficient data -> keep prior state" behavior cannot be preserved here; it would need to
live in whatever stateful caller invokes this function repeatedly (out of scope for this
migration step, same as GridLevels -> TradeIntent in strategy/grid.py). Preserved instead as
an explicit precondition: raises ValueError below that bar count rather than guessing or
fabricating a FLAT/stale signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from mt5_mcp_trading.domain.models import MarketBar, Signal
from mt5_mcp_trading.features.atr import atr as compute_atr
from mt5_mcp_trading.features.macd import macd as compute_macd


@dataclass(frozen=True, slots=True)
class RunnerStrategyConfig:
    fast: int = 12
    slow: int = 26
    min_bars_floor: int = 30  # legacy: hardcoded literal 30 in `max(30, slow + 5)`
    # SL/TP fields below have NO legacy precedent -- ema_crossover_core_multi.py's runner never
    # attached a stop-loss/take-profit to its MARKET orders at all (see this module's docstring).
    # New, project-original design, needed only because the real McpOrderExecutor mandates
    # non-zero SL/TP for MARKET orders (see compute_stop_distances()). Defaults were initially an
    # untuned placeholder (sl_atr_mult=1.5), later replaced by Phase 8's tuning result: a
    # one-factor-at-a-time sweep (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md, Step 5) found
    # sl_atr_mult=3.0 a genuine interior peak on the training window, then Step 6 confirmed the
    # improvement out-of-sample on a held-out test window never used for tuning (expectancy
    # -0.241R -> -0.100R, max drawdown 463R -> 63R) -- a real, evidence-backed change, not a
    # guess. Still net-negative overall; this reduces the loss, it does not claim a positive edge.
    atr_period: int = 14  # matches GridStrategyConfig.atr_period's default
    sl_atr_mult: float = 3.0
    tp_atr_mult: float = 6.0  # 2:1 reward:risk, unchanged
    min_stop_distance_points: float = 10.0  # matches GridStrategyConfig.min_step_points's floor
    # NO legacy precedent either -- ema_crossover_core_multi.py's runner had no re-entry
    # throttle at all, just like it had no SL/TP. New, project-original: run_runner_cycle() had
    # no protection against re-entering the same direction repeatedly beyond the raw lot-based
    # exposure cap, a real gap only surfaced by backtesting at realistic scale
    # (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md, "Fix runner's re-entry throttle" -- a
    # 10,000-cycle real run produced 9,881 trades and -0.159R expectancy, traced to this).
    # Default 1: at most one open runner position per magic at a time, the simplest, most
    # conservative choice -- open to tuning, like every other field here.
    max_concurrent_positions: int = 1


def _min_required_bars(config: RunnerStrategyConfig) -> int:
    return max(config.min_bars_floor, config.slow + 5)


def runner_signal(bars: list[MarketBar], config: RunnerStrategyConfig) -> Signal:
    required = _min_required_bars(config)
    if len(bars) < required:
        raise ValueError(
            f"runner_signal requires at least {required} bars "
            f"(max({config.min_bars_floor}, slow+5)), got {len(bars)}"
        )

    closes = [b.close for b in bars]
    macd_value = compute_macd(closes, fast=config.fast, slow=config.slow)

    if macd_value > 0:
        direction = "LONG"
    elif macd_value < 0:
        direction = "SHORT"
    else:
        direction = "FLAT"

    return Signal(
        symbol=bars[-1].symbol,
        strategy_name="runner",
        direction=direction,
        timestamp=bars[-1].time,
        rationale=f"macd={macd_value:.6f} fast={config.fast} slow={config.slow}",
    )


def compute_stop_distances(
    bars: Sequence[MarketBar], point: float, config: RunnerStrategyConfig
) -> tuple[float, float]:
    """Returns (sl_distance, tp_distance) in price units, both > 0 -- how far from the fill
    price runner's MARKET orders' stop-loss/take-profit should sit. New, project-original
    design: the legacy runner never attached SL/TP at all (see this module's docstring), so
    there is no legacy formula to preserve here. ATR-based, reusing features/atr.py's existing
    helper (the same one strategy/grid.py already uses for its own step/tp sizing) -- falls back
    to a fixed points floor when ATR can't be computed (insufficient bars), mirroring
    strategy/grid.py's compute_grid_levels() fallback."""
    atr_value = compute_atr(bars, config.atr_period)
    base = atr_value if atr_value > 0 else config.min_stop_distance_points * point
    return base * config.sl_atr_mult, base * config.tp_atr_mult
