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

from mt5_mcp_trading.domain.models import MarketBar, Signal
from mt5_mcp_trading.features.macd import macd as compute_macd


@dataclass(frozen=True, slots=True)
class RunnerStrategyConfig:
    fast: int = 12
    slow: int = 26
    min_bars_floor: int = 30  # legacy: hardcoded literal 30 in `max(30, slow + 5)`


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
