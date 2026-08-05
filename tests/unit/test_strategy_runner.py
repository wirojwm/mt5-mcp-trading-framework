from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import MarketBar
from mt5_mcp_trading.strategy.runner import (
    RunnerStrategyConfig,
    compute_stop_distances,
    runner_signal,
)

from tests.unit.test_features_macd import DOWNWARD_CLOSES, UPWARD_CLOSES


def _bars_from_closes(closes: list[float], symbol: str = "BTCUSD") -> list[MarketBar]:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(
            symbol=symbol, timeframe="M5", time=now, open=c, high=c, low=c, close=c,
            tick_volume=1, spread=1,
        )
        for c in closes
    ]


def test_long_signal_on_upward_series() -> None:
    signal = runner_signal(_bars_from_closes(UPWARD_CLOSES), RunnerStrategyConfig())
    assert signal.direction == "LONG"
    assert signal.symbol == "BTCUSD"
    assert signal.strategy_name == "runner"
    assert "macd=2.070016" in signal.rationale


def test_short_signal_on_downward_series() -> None:
    signal = runner_signal(_bars_from_closes(DOWNWARD_CLOSES), RunnerStrategyConfig())
    assert signal.direction == "SHORT"


def test_flat_signal_on_constant_series() -> None:
    signal = runner_signal(_bars_from_closes([100.0] * 40), RunnerStrategyConfig())
    assert signal.direction == "FLAT"


def test_signal_timestamp_and_symbol_come_from_last_bar() -> None:
    bars = _bars_from_closes(UPWARD_CLOSES, symbol="XAUUSD")
    signal = runner_signal(bars, RunnerStrategyConfig())
    assert signal.symbol == "XAUUSD"
    assert signal.timestamp == bars[-1].time


def test_raises_below_the_stricter_caller_side_bar_requirement() -> None:
    # required = max(30, slow+5) = max(30, 31) = 31 for defaults (slow=26) -- confirms the
    # *stricter* legacy _loop() guard is enforced here, not just _macd()'s own len>=26 guard.
    config = RunnerStrategyConfig()
    bars = _bars_from_closes(UPWARD_CLOSES[:26])  # passes macd()'s own guard (26 >= 26)...
    assert len(bars) < max(config.min_bars_floor, config.slow + 5)  # ...but not this one (31)
    with pytest.raises(ValueError):
        runner_signal(bars, config)


def test_min_required_bars_uses_slow_plus_5_when_it_exceeds_the_floor() -> None:
    # With a larger slow period, slow+5 should dominate over the 30 floor.
    config = RunnerStrategyConfig(fast=12, slow=40)
    bars = _bars_from_closes(UPWARD_CLOSES * 2)[:44]  # 44 < 40+5=45
    with pytest.raises(ValueError):
        runner_signal(bars, config)


def test_exactly_enough_bars_does_not_raise() -> None:
    config = RunnerStrategyConfig()
    required = max(config.min_bars_floor, config.slow + 5)
    bars = _bars_from_closes((UPWARD_CLOSES * 2)[:required])
    signal = runner_signal(bars, config)  # must not raise
    assert signal.direction in ("LONG", "SHORT", "FLAT")


# ---------- compute_stop_distances() -- new, no legacy precedent (see module docstring) ----------

def test_compute_stop_distances_uses_atr_when_available() -> None:
    # _bars_from_closes sets high=low=close, so true range across bars is |close[i]-close[i-1]|
    # -- UPWARD_CLOSES moves every bar, giving a genuinely positive ATR, not the zero-bars
    # fallback.
    bars = _bars_from_closes(UPWARD_CLOSES)
    config = RunnerStrategyConfig()
    sl_distance, tp_distance = compute_stop_distances(bars, point=0.01, config=config)

    assert sl_distance > 0
    assert tp_distance > 0
    assert tp_distance > sl_distance  # tp_atr_mult (6.0) > sl_atr_mult (3.0) by default
    assert tp_distance == pytest.approx(sl_distance * (config.tp_atr_mult / config.sl_atr_mult))


def test_compute_stop_distances_falls_back_to_floor_when_atr_is_zero() -> None:
    # Flat, constant closes -> zero true range every bar -> atr() returns 0.0 -> floor fallback.
    bars = _bars_from_closes([100.0] * 5)
    config = RunnerStrategyConfig(min_stop_distance_points=10.0, sl_atr_mult=1.5, tp_atr_mult=3.0)
    point = 0.01

    sl_distance, tp_distance = compute_stop_distances(bars, point=point, config=config)

    assert sl_distance == pytest.approx(10.0 * point * 1.5)
    assert tp_distance == pytest.approx(10.0 * point * 3.0)
