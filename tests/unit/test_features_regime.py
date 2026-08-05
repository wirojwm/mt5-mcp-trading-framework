from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import MarketBar
from mt5_mcp_trading.features.regime import efficiency_ratio


def _bars_from_closes(closes: list[float]) -> list[MarketBar]:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(
            symbol="TEST", timeframe="M1", time=now, open=c, high=c, low=c, close=c,
            tick_volume=1, spread=1,
        )
        for c in closes
    ]


def test_pure_uptrend_has_efficiency_ratio_of_one() -> None:
    # Monotonic path: net change == total path length, so ER == 1.0 exactly.
    bars = _bars_from_closes([100.0, 101.0, 102.0, 103.0, 104.0])
    assert efficiency_ratio(bars, period=4) == pytest.approx(1.0, abs=1e-12)


def test_pure_downtrend_also_has_efficiency_ratio_of_one() -> None:
    # ER uses |net_change|, direction-agnostic -- a clean downtrend is just as "efficient".
    bars = _bars_from_closes([104.0, 103.0, 102.0, 101.0, 100.0])
    assert efficiency_ratio(bars, period=4) == pytest.approx(1.0, abs=1e-12)


def test_symmetric_round_trip_has_efficiency_ratio_of_zero() -> None:
    # Ends exactly where it started (net_change=0) after real back-and-forth movement
    # (path_length>0) -- the textbook "ranging" case.
    bars = _bars_from_closes([100.0, 102.0, 98.0, 102.0, 100.0])
    assert efficiency_ratio(bars, period=4) == pytest.approx(0.0, abs=1e-12)


def test_partial_chop_gives_a_value_strictly_between_zero_and_one() -> None:
    # net_change = |103-100| = 3; path_length = 2+1+2+2 = 7 -> ER = 3/7.
    bars = _bars_from_closes([100.0, 102.0, 101.0, 103.0, 105.0])
    # closes[-5:] = the whole list here (period=4 -> 5 closes): 100,102,101,103,105
    # net_change = |105-100| = 5; path_length = 2+1+2+2 = 7 -> ER = 5/7
    assert efficiency_ratio(bars, period=4) == pytest.approx(5.0 / 7.0, abs=1e-12)


def test_flat_closes_return_zero_not_a_division_error() -> None:
    bars = _bars_from_closes([100.0] * 5)
    assert efficiency_ratio(bars, period=4) == 0.0


def test_insufficient_bars_returns_zero() -> None:
    bars = _bars_from_closes([100.0, 101.0, 102.0])
    assert efficiency_ratio(bars, period=4) == 0.0


def test_only_uses_the_trailing_window_not_bars_before_it() -> None:
    # A big move far in the past must not leak into a later, calmer window's reading.
    bars = _bars_from_closes([50.0, 200.0] + [100.0, 101.0, 100.0, 101.0, 100.0])
    ratio = efficiency_ratio(bars, period=4)
    assert ratio == pytest.approx(0.0, abs=1e-12)


def test_period_less_than_one_raises() -> None:
    bars = _bars_from_closes([100.0, 101.0])
    with pytest.raises(ValueError):
        efficiency_ratio(bars, period=0)
