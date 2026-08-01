"""
Exponential moving average.

Ported from the legacy project's launcher_grid.py `_ema()` (pandas `.ewm(span=period,
adjust=False).mean()`), reimplemented as dependency-free pure Python rather than imported or
copied verbatim -- per this project's rule against depending on the legacy codebase. Verified
numerically identical to the pandas formulation for a range of periods and inputs (see
tests/unit/test_features_ema.py); the recursion below is the standard `adjust=False` EMA
definition, which pandas' ewm() also implements internally, so the equivalence isn't
coincidental.

alpha = 2 / (period + 1); ema[0] = x[0]; ema[i] = alpha * x[i] + (1 - alpha) * ema[i-1]

Deliberately does NOT special-case period <= 1 the way the legacy version did (`if period <= 1:
return last value`): algebraically, period=1 gives alpha=1.0, so the recursion already reduces
to "last value" on its own. Confirmed by test, not just by inspection.
"""

from __future__ import annotations

from typing import Sequence


def ema(values: Sequence[float], period: int) -> float:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if not values:
        return 0.0

    alpha = 2.0 / (period + 1.0)
    result = float(values[0])
    for v in values[1:]:
        result = v * alpha + result * (1.0 - alpha)
    return result
