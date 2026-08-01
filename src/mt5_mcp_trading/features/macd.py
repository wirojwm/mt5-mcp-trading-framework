"""
MACD, ported from the legacy project's ema_crossover_core_multi.py `_macd()`.

Deliberately NOT the textbook MACD (fast-EMA-over-full-history minus slow-EMA-over-full-
history). The legacy formula computes *both* EMAs over the same truncated window of only the
most recent `slow` closes:

    macd = ema(closes[-slow:], fast) - ema(closes[-slow:], slow)   # when fast < slow

This is a real behavioral choice in the legacy code (confirmed by reading it directly, not
assumed) and changes the numeric result versus a full-history MACD, since EMA is not
window-invariant -- the recursion restarts from closes[-slow:][0] rather than the true start
of history. Preserved exactly; "preserve the intended behavior" means preserving this, not
correcting it to the textbook definition.

Guard, also preserved exactly: if len(closes) < max(fast, slow), returns 0.0 rather than
raising -- this function mirrors that (see strategy/runner.py for where a *stricter*,
caller-side bar-sufficiency requirement is enforced instead, matching the legacy caller's
own additional guard).

If fast >= slow (a misconfiguration), the legacy formula uses `slow` for both EMA calls,
making the result exactly 0.0 -- also preserved, not treated as an error.

Reuses features.ema rather than porting a second, separate EMA implementation:
ema_crossover_core_multi.py's own `_ema()` is mathematically identical to launcher_grid.py's
`_ema()` for any period >= 1 (both are the standard adjust=False EMA recursion) -- the legacy
project had two near-duplicate implementations of the same formula, and unifying them here is
a reuse decision, not a behavior change.
"""

from __future__ import annotations

from typing import Sequence

from mt5_mcp_trading.features.ema import ema


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26) -> float:
    if len(closes) < max(fast, slow):
        return 0.0

    window = closes[-slow:]
    fast_period = fast if fast < slow else slow
    return ema(window, fast_period) - ema(window, slow)
