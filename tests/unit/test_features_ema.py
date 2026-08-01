"""
Expected values below were cross-checked against pandas' `.ewm(span=period,
adjust=False).mean()` directly (the legacy project's actual formula) in a standalone scratch
script, not derived from this implementation itself or from importing the legacy code.
"""

from __future__ import annotations

import pytest

from mt5_mcp_trading.features.ema import ema

CLOSES = [10.0, 10.5, 11.0, 10.8, 11.2, 11.5, 11.3, 11.7, 12.0, 11.9]


@pytest.mark.parametrize(
    "period, expected",
    [
        (1, 11.9),
        (3, 11.8205078125),
        (5, 11.658888380836256),
    ],
)
def test_ema_matches_pandas_ewm_adjust_false(period: int, expected: float) -> None:
    assert ema(CLOSES, period) == pytest.approx(expected, abs=1e-12)


def test_ema_period_1_equals_last_value_without_special_casing() -> None:
    # Confirms the algebraic claim in ema.py's docstring: alpha=1.0 at period=1 makes the
    # recursion reduce to "last value" on its own, no special-case branch needed.
    assert ema(CLOSES, 1) == CLOSES[-1]


def test_ema_empty_input_returns_zero() -> None:
    assert ema([], 5) == 0.0


def test_ema_single_value() -> None:
    assert ema([42.0], 5) == 42.0


def test_ema_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError):
        ema(CLOSES, 0)
