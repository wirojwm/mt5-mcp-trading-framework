from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import MarketBar
from mt5_mcp_trading.features.atr import atr as compute_atr
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


def test_compute_stop_distances_falls_back_to_points_floor_when_atr_is_zero() -> None:
    # Flat, constant closes -> zero true range every bar -> atr() returns 0.0 -> floor fallback.
    # min_stop_distance_fraction_of_price=0.0 isolates the points-floor path from the price-
    # fraction floor added below, so this stays a regression test for the pre-existing behavior.
    bars = _bars_from_closes([100.0] * 5)
    config = RunnerStrategyConfig(
        min_stop_distance_points=10.0, sl_atr_mult=1.5, tp_atr_mult=3.0,
        min_stop_distance_fraction_of_price=0.0,
    )
    point = 0.01

    sl_distance, tp_distance = compute_stop_distances(bars, point=point, config=config)

    assert sl_distance == pytest.approx(10.0 * point * 1.5)
    assert tp_distance == pytest.approx(10.0 * point * 3.0)


# ---------- min_stop_distance_fraction_of_price floor (2026-08-11 retcode-10016 root cause) ----------

def test_compute_stop_distances_points_floor_is_too_small_for_a_high_priced_instrument() -> None:
    # Reproduces the actual bug: on a BTCUSD-like price, the old points-only floor (10 * point =
    # $0.10) is nowhere near the ~1% broker-side minimum Phase 6 live-confirmed -- so with the
    # fraction floor disabled, the pre-fix distance really was this tiny.
    bars = _bars_from_closes([63962.57] * 5)
    config = RunnerStrategyConfig(min_stop_distance_fraction_of_price=0.0)
    point = 0.01

    sl_distance, _ = compute_stop_distances(bars, point=point, config=config)

    assert sl_distance == pytest.approx(10.0 * point * config.sl_atr_mult)
    assert sl_distance / bars[-1].close < 0.0001  # << 1%, exactly the failure mode observed live


def test_compute_stop_distances_fraction_floor_dominates_when_atr_is_zero() -> None:
    # Same flat, high-priced series, but with the fix's default config (fraction floor enabled).
    bars = _bars_from_closes([63962.57] * 5)
    config = RunnerStrategyConfig()  # default min_stop_distance_fraction_of_price=0.01
    point = 0.01

    sl_distance, tp_distance = compute_stop_distances(bars, point=point, config=config)

    expected_base = bars[-1].close * config.min_stop_distance_fraction_of_price
    assert sl_distance == pytest.approx(expected_base * config.sl_atr_mult)
    assert tp_distance == pytest.approx(expected_base * config.tp_atr_mult)
    assert sl_distance / bars[-1].close == pytest.approx(
        config.min_stop_distance_fraction_of_price * config.sl_atr_mult
    )


def test_compute_stop_distances_fraction_floor_dominates_a_small_but_positive_atr() -> None:
    # The exact scenario that actually failed live (ticket 171979510, 2026-08-11): a genuinely
    # positive but small ATR on a high-priced instrument. Small, realistic wiggle around ~$63,962
    # keeps ATR in the same single-digit-dollar range observed on the real M1 series that day
    # (~$4.24), which the old code would have used unfloored.
    wiggle = [63962.57, 63965.0, 63960.0, 63963.5, 63961.0, 63964.0, 63962.0, 63966.0,
              63959.5, 63963.0, 63961.5, 63965.5, 63960.5, 63963.0, 63962.57]
    bars = _bars_from_closes(wiggle)
    config = RunnerStrategyConfig()
    point = 0.01

    atr_only_sl = compute_atr(bars, config.atr_period) * config.sl_atr_mult
    sl_distance, tp_distance = compute_stop_distances(bars, point=point, config=config)

    price_floor_sl = bars[-1].close * config.min_stop_distance_fraction_of_price * config.sl_atr_mult
    assert atr_only_sl < price_floor_sl  # confirms this case actually needs the new floor
    assert sl_distance == pytest.approx(price_floor_sl)
    assert sl_distance / bars[-1].close >= config.min_stop_distance_fraction_of_price
    assert tp_distance == pytest.approx(sl_distance * (config.tp_atr_mult / config.sl_atr_mult))


def test_compute_stop_distances_fraction_floor_does_not_shrink_a_large_atr() -> None:
    # When ATR is already comfortably above the fraction floor (a genuinely volatile market,
    # swinging several % per bar), the floor must be a no-op -- this fix must never shrink a
    # distance the old code would have produced. UPWARD_CLOSES's own ~0.7% swings turn out to sit
    # *below* the 1% fraction floor, so a distinctly larger-swing series is used here instead.
    volatile_closes = [100.0, 108.0, 96.0, 110.0, 94.0, 112.0, 93.0, 111.0,
                        95.0, 109.0, 97.0, 107.0, 96.0, 108.0, 95.0]
    bars = _bars_from_closes(volatile_closes)
    config = RunnerStrategyConfig()
    point = 0.01

    atr_value = compute_atr(bars, config.atr_period)
    price_floor = bars[-1].close * config.min_stop_distance_fraction_of_price
    assert atr_value > price_floor  # precondition: ATR genuinely dominates here

    sl_distance, _ = compute_stop_distances(bars, point=point, config=config)
    assert sl_distance == pytest.approx(atr_value * config.sl_atr_mult)
