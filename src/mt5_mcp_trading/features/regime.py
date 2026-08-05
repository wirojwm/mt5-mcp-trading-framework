"""
Kaufman's Efficiency Ratio (Phase 8 Step 7, docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): a
standard, simple measure of how "trending" vs "ranging" a price window is -- net displacement
over the window divided by the total path length within it.

    ER = |close[-1] - close[-1-period]| / sum(|close[i] - close[i-1]| for the same window)

ER close to 1 means price moved efficiently in one direction (trending, little backtracking);
ER close to 0 means price churned back and forth without net progress (ranging/choppy) -- the
total path length is large relative to how far price actually ended up moving. No new dependency,
no new domain concept -- same pure-function-over-MarketBar.close pattern as features/atr.py and
features/ema.py, and the same "period < 1 raises, insufficient bars returns 0.0" convention as
features/atr.py, for the same reason: callers here always have a defined fallback for
"insufficient data", never a case where 0.0 could be silently mistaken for a real reading (an ER
of exactly 0.0 --a perfectly flat round-trip-- is also a legitimate real value, callers needing to
distinguish "no data" from "genuinely flat" must check len(bars) themselves, same as atr()).
"""

from __future__ import annotations

from typing import Sequence

from mt5_mcp_trading.domain.models import MarketBar


def efficiency_ratio(bars: Sequence[MarketBar], period: int) -> float:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if len(bars) < period + 1:
        return 0.0

    closes = [b.close for b in bars[-(period + 1):]]
    net_change = abs(closes[-1] - closes[0])
    path_length = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path_length <= 0:
        return 0.0
    return net_change / path_length
