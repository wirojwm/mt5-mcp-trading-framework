"""
Expected values cross-checked against the legacy formula (ATR via pandas rolling-mean-of-
true-range, EMA via pandas .ewm(adjust=False), then step_price = max(min_step_points*point,
atr*step_mult), tp_price = max(min_step_points*point, atr*step_mult*1.2)) in a standalone
scratch script -- not derived from this implementation itself or from importing the legacy
code.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import MarketBar
from mt5_mcp_trading.strategy.grid import GridStrategyConfig, compute_grid_levels

HIGH = [10.2, 10.7, 11.3, 11.0, 11.4, 11.8, 11.6, 11.9, 12.3, 12.1, 12.4, 12.0, 12.6, 12.8, 12.5]
LOW = [9.8, 10.3, 10.8, 10.5, 10.9, 11.2, 11.1, 11.4, 11.8, 11.6, 11.9, 11.5, 12.1, 12.3, 12.0]
CLOSE = [10.0, 10.5, 11.0, 10.8, 11.2, 11.5, 11.3, 11.7, 12.0, 11.9, 12.2, 11.8, 12.3, 12.5, 12.2]
POINT = 0.01


def _bars(n: int, symbol: str = "TEST") -> list[MarketBar]:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(
            symbol=symbol, timeframe="M1", time=now, open=CLOSE[i], high=HIGH[i], low=LOW[i],
            close=CLOSE[i], tick_volume=1, spread=1,
        )
        for i in range(n)
    ]


def test_normal_case_atr_scaled_step_matches_legacy_formula() -> None:
    config = GridStrategyConfig(atr_period=14, center_ema_period=5, center_mode="ema", step_mult=1.0)
    result = compute_grid_levels(_bars(15), point=POINT, config=config)

    assert result.symbol == "TEST"
    assert result.atr == pytest.approx(0.6, abs=1e-12)
    assert result.center == pytest.approx(12.170717811468151, abs=1e-9)
    assert result.step_price == pytest.approx(0.6, abs=1e-12)  # atr*step_mult (0.6) beats floor (0.1)
    assert result.tp_price == pytest.approx(0.72, abs=1e-12)  # atr*step_mult*1.2
    assert result.sl_price == pytest.approx(1.2, abs=1e-12)  # atr*sl_atr_mult (default 2.0)
    assert result.buy_price == pytest.approx(11.570717811468151, abs=1e-9)
    assert result.sell_price == pytest.approx(12.77071781146815, abs=1e-9)


def test_step_and_tp_floor_at_min_step_points_when_atr_scaled_value_is_smaller() -> None:
    # step_mult tiny enough that atr*step_mult << min_step_points*point -> floor wins for both.
    config = GridStrategyConfig(atr_period=14, center_ema_period=5, step_mult=0.001, min_step_points=10.0)
    result = compute_grid_levels(_bars(15), point=POINT, config=config)

    assert result.step_price == pytest.approx(0.1, abs=1e-12)  # 10 * point
    assert result.tp_price == pytest.approx(0.1, abs=1e-12)


def test_sl_floor_at_min_step_points_when_atr_scaled_value_is_smaller() -> None:
    # sl_atr_mult tiny enough that atr*sl_atr_mult << min_step_points*point -> floor wins.
    config = GridStrategyConfig(atr_period=14, center_ema_period=5, sl_atr_mult=0.001, min_step_points=10.0)
    result = compute_grid_levels(_bars(15), point=POINT, config=config)

    assert result.sl_price == pytest.approx(0.1, abs=1e-12)  # 10 * point


def test_tp_always_tracks_step_mult_even_when_overridden() -> None:
    # Regression guard for the bug caught during implementation: tp must derive from
    # whatever step_mult is configured, not from an independently-stale default.
    config_a = GridStrategyConfig(atr_period=14, step_mult=0.5)
    config_b = GridStrategyConfig(atr_period=14, step_mult=2.0)
    result_a = compute_grid_levels(_bars(15), point=POINT, config=config_a)
    result_b = compute_grid_levels(_bars(15), point=POINT, config=config_b)

    assert result_a.tp_price == pytest.approx(result_a.step_price * 1.2, abs=1e-12)
    assert result_b.tp_price == pytest.approx(result_b.step_price * 1.2, abs=1e-12)
    assert result_a.tp_price != result_b.tp_price


def test_sl_is_independent_of_step_mult_and_tracks_only_sl_atr_mult() -> None:
    # sl_price must NOT move when step_mult changes (unlike tp_price, which is deliberately
    # coupled to it) -- and must move when sl_atr_mult changes, proving the two are decoupled.
    config_a = GridStrategyConfig(atr_period=14, step_mult=0.5, sl_atr_mult=2.0)
    config_b = GridStrategyConfig(atr_period=14, step_mult=2.0, sl_atr_mult=2.0)
    config_c = GridStrategyConfig(atr_period=14, step_mult=0.5, sl_atr_mult=3.0)
    result_a = compute_grid_levels(_bars(15), point=POINT, config=config_a)
    result_b = compute_grid_levels(_bars(15), point=POINT, config=config_b)
    result_c = compute_grid_levels(_bars(15), point=POINT, config=config_c)

    assert result_a.sl_price == pytest.approx(result_b.sl_price, abs=1e-12)  # step_mult changed, sl unaffected
    assert result_a.sl_price != result_c.sl_price  # sl_atr_mult changed, sl DOES move
    assert result_c.sl_price == pytest.approx(result_a.sl_price * 1.5, abs=1e-12)  # 3.0 / 2.0


def test_atr_fallback_when_not_enough_bars_uses_last_close_and_unscaled_floor() -> None:
    # Fewer than period+1 bars -> features.atr returns 0.0 -> fallback branch:
    # center = last close, step_price = tp_price = sl_price = min_step_points * point (NOT *1.2
    # for tp, NOT *sl_atr_mult for sl; that asymmetry is faithfully preserved from the legacy
    # fallback for tp, and sl mirrors the same unscaled-floor shape by design).
    config = GridStrategyConfig(atr_period=14, min_step_points=10.0)
    bars = _bars(10)  # < atr_period + 1
    result = compute_grid_levels(bars, point=POINT, config=config)

    assert result.atr == 0.0
    assert result.center == bars[-1].close
    assert result.step_price == pytest.approx(0.1, abs=1e-12)
    assert result.tp_price == pytest.approx(0.1, abs=1e-12)  # not 0.12 -- legacy quirk preserved
    assert result.sl_price == pytest.approx(0.1, abs=1e-12)  # not 0.2 -- same unscaled-floor shape
    assert result.buy_price == pytest.approx(bars[-1].close - 0.1, abs=1e-12)
    assert result.sell_price == pytest.approx(bars[-1].close + 0.1, abs=1e-12)


def test_center_mode_last_ignores_ema_and_uses_last_close() -> None:
    config = GridStrategyConfig(atr_period=14, center_mode="last", step_mult=1.0)
    result = compute_grid_levels(_bars(15), point=POINT, config=config)
    assert result.center == CLOSE[14]


def test_empty_bars_raises() -> None:
    with pytest.raises(ValueError):
        compute_grid_levels([], point=POINT, config=GridStrategyConfig())


def test_buy_below_sell_above_center_symmetrically() -> None:
    config = GridStrategyConfig(atr_period=14, step_mult=0.7)
    result = compute_grid_levels(_bars(15), point=POINT, config=config)
    assert result.center - result.buy_price == pytest.approx(result.sell_price - result.center, abs=1e-12)
    assert result.center - result.buy_price == pytest.approx(result.step_price, abs=1e-12)
