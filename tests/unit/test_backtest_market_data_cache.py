from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mt5_mcp_trading.backtest.market_data_cache import (
    cache_path,
    load_bars,
    merge_bars,
    save_bars,
    split_bars,
)
from mt5_mcp_trading.domain.models import MarketBar

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(minute: int, close: float, symbol: str = SYMBOL, timeframe: str = TIMEFRAME) -> MarketBar:
    return MarketBar(
        symbol=symbol, timeframe=timeframe, time=BASE + timedelta(minutes=minute),
        open=close - 1, high=close + 1, low=close - 2, close=close, tick_volume=10, spread=2,
    )


def test_cache_path_builds_expected_filename(tmp_path: Path) -> None:
    assert cache_path(tmp_path, SYMBOL, TIMEFRAME) == tmp_path / "BTCUSD_M1.csv"


def test_load_bars_returns_empty_list_when_file_does_not_exist(tmp_path: Path) -> None:
    path = cache_path(tmp_path, SYMBOL, TIMEFRAME)
    assert load_bars(path, SYMBOL, TIMEFRAME) == []


def test_save_then_load_round_trips_bars_exactly(tmp_path: Path) -> None:
    path = cache_path(tmp_path, SYMBOL, TIMEFRAME)
    bars = [_bar(0, 100.0), _bar(1, 101.5), _bar(2, 99.25)]

    save_bars(path, bars)
    loaded = load_bars(path, SYMBOL, TIMEFRAME)

    assert loaded == sorted(bars, key=lambda b: b.time)


def test_save_bars_sorts_before_writing_regardless_of_input_order(tmp_path: Path) -> None:
    path = cache_path(tmp_path, SYMBOL, TIMEFRAME)
    out_of_order = [_bar(2, 99.25), _bar(0, 100.0), _bar(1, 101.5)]

    save_bars(path, out_of_order)
    loaded = load_bars(path, SYMBOL, TIMEFRAME)

    assert [b.time for b in loaded] == [BASE, BASE + timedelta(minutes=1), BASE + timedelta(minutes=2)]


def test_save_bars_raises_on_empty_list(tmp_path: Path) -> None:
    path = cache_path(tmp_path, SYMBOL, TIMEFRAME)
    with pytest.raises(ValueError):
        save_bars(path, [])
    assert not path.exists()  # never created a file for a rejected write


def test_save_bars_raises_on_mixed_symbol(tmp_path: Path) -> None:
    path = cache_path(tmp_path, SYMBOL, TIMEFRAME)
    mixed = [_bar(0, 100.0, symbol="BTCUSD"), _bar(1, 100.0, symbol="EURUSD")]
    with pytest.raises(ValueError):
        save_bars(path, mixed)


def test_save_bars_raises_on_mixed_timeframe(tmp_path: Path) -> None:
    path = cache_path(tmp_path, SYMBOL, TIMEFRAME)
    mixed = [_bar(0, 100.0, timeframe="M1"), _bar(1, 100.0, timeframe="M5")]
    with pytest.raises(ValueError):
        save_bars(path, mixed)


def test_merge_bars_combines_and_sorts() -> None:
    existing = [_bar(0, 100.0), _bar(2, 99.0)]
    new = [_bar(1, 101.0), _bar(3, 98.0)]

    merged = merge_bars(existing, new)

    assert [b.time for b in merged] == [BASE + timedelta(minutes=i) for i in range(4)]


def test_merge_bars_dedups_by_time_new_wins() -> None:
    existing = [_bar(0, 100.0)]
    new = [_bar(0, 999.0)]  # same timestamp, different close -- simulates a re-fetch

    merged = merge_bars(existing, new)

    assert len(merged) == 1
    assert merged[0].close == 999.0


def test_merge_then_save_then_load_extends_an_existing_cache(tmp_path: Path) -> None:
    """The realistic incremental-extension flow this module exists to support."""
    path = cache_path(tmp_path, SYMBOL, TIMEFRAME)
    save_bars(path, [_bar(0, 100.0), _bar(1, 101.0)])

    already_cached = load_bars(path, SYMBOL, TIMEFRAME)
    freshly_fetched = [_bar(1, 101.0), _bar(2, 102.0)]  # overlaps by one bar, adds one new
    save_bars(path, merge_bars(already_cached, freshly_fetched))

    loaded = load_bars(path, SYMBOL, TIMEFRAME)
    assert [b.time for b in loaded] == [BASE, BASE + timedelta(minutes=1), BASE + timedelta(minutes=2)]


# ---------- split_bars (Phase 8 Step 5 train/test split) ----------

def test_split_bars_default_fraction_splits_80_20() -> None:
    bars = [_bar(i, 100.0 + i) for i in range(100)]
    train, test = split_bars(bars)
    assert len(train) == 80
    assert len(test) == 20


def test_split_bars_train_is_strictly_earlier_than_test() -> None:
    bars = [_bar(i, 100.0 + i) for i in range(100)]
    train, test = split_bars(bars, train_fraction=0.8)
    assert train[-1].time < test[0].time
    assert train == bars[:80]
    assert test == bars[80:]


def test_split_bars_covers_every_bar_exactly_once() -> None:
    bars = [_bar(i, 100.0 + i) for i in range(37)]  # an awkward, non-round count
    train, test = split_bars(bars, train_fraction=0.8)
    assert train + test == bars


def test_split_bars_both_sides_nonempty_even_at_extreme_fractions() -> None:
    bars = [_bar(i, 100.0 + i) for i in range(10)]
    train, test = split_bars(bars, train_fraction=0.99)
    assert len(train) >= 1
    assert len(test) >= 1


def test_split_bars_raises_on_empty_bars() -> None:
    with pytest.raises(ValueError):
        split_bars([])


def test_split_bars_raises_on_out_of_range_fraction() -> None:
    bars = [_bar(0, 100.0)]
    with pytest.raises(ValueError):
        split_bars(bars, train_fraction=0.0)
    with pytest.raises(ValueError):
        split_bars(bars, train_fraction=1.0)
    with pytest.raises(ValueError):
        split_bars(bars, train_fraction=1.5)
