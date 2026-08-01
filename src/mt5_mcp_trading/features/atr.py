"""
Average True Range: a simple rolling mean of true range, matching the legacy project's
launcher_grid.py `_atr()` exactly -- NOT Wilder's smoothed ATR, despite the name. Reimplemented
as dependency-free pure Python (no pandas/numpy) rather than imported or copied verbatim, and
verified numerically identical to the pandas formulation (see tests/unit/test_features_atr.py).

true_range[i] = max(high[i]-low[i], |high[i]-prev_close[i]|, |low[i]-prev_close[i]|)
where prev_close[i] = close[i-1] for i>0, and close[0] for i=0 (the legacy code fills the
first bar's "previous close" with its own close, via pandas' `.shift(1).fillna(c[0])`).

atr = mean(true_range[-period:])

Faithfully preserves an odd legacy guard: requires len(bars) >= period + 1, one bar MORE than
pandas' rolling(period).mean() actually needs (confirmed by scratch-testing against pandas
directly: rolling(14) is already non-NaN with exactly 14 bars, but the legacy code's explicit
`len(df) < period + 1` check discards that case anyway and returns 0.0). This function
replicates the legacy *function's* behavior, not the mathematical minimum.
"""

from __future__ import annotations

from typing import Sequence

from mt5_mcp_trading.domain.models import MarketBar


def atr(bars: Sequence[MarketBar], period: int) -> float:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if len(bars) < period + 1:
        return 0.0

    true_ranges: list[float] = []
    for i, bar in enumerate(bars):
        prev_close = bars[i - 1].close if i > 0 else bar.close
        true_ranges.append(
            max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        )

    window = true_ranges[-period:]
    return sum(window) / period
