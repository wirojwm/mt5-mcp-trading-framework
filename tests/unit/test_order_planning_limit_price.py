"""
Expected values cross-checked against the legacy mt5_bridge.normalize_price() formula in a
standalone scratch script -- not derived from this implementation itself or from importing
the legacy code.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import SymbolInfo, Tick
from mt5_mcp_trading.order_planning.limit_price import normalize_limit_price

SYMBOL_INFO = SymbolInfo(
    symbol="BTCUSD", digits=2, point=0.01, volume_min=0.01, volume_max=100.0,
    volume_step=0.01, stops_level=10, freeze_level=5,
)
TICK = Tick(symbol="BTCUSD", bid=100.00, ask=100.02, time=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_buy_limit_already_far_enough_is_unchanged() -> None:
    assert normalize_limit_price(99.50, "BUY_LIMIT", SYMBOL_INFO, TICK) == pytest.approx(99.5, abs=1e-9)


def test_buy_limit_too_close_gets_pushed_further_below_bid() -> None:
    assert normalize_limit_price(99.90, "BUY_LIMIT", SYMBOL_INFO, TICK) == pytest.approx(99.8, abs=1e-9)


def test_sell_limit_already_far_enough_is_unchanged() -> None:
    assert normalize_limit_price(100.50, "SELL_LIMIT", SYMBOL_INFO, TICK) == pytest.approx(100.5, abs=1e-9)


def test_sell_limit_too_close_gets_pushed_further_above_ask() -> None:
    assert normalize_limit_price(100.10, "SELL_LIMIT", SYMBOL_INFO, TICK) == pytest.approx(100.22, abs=1e-9)


def test_price_is_snapped_to_tick_size() -> None:
    # 99.503 should snap to 99.50 (point=0.01) before any gap adjustment.
    assert normalize_limit_price(99.503, "BUY_LIMIT", SYMBOL_INFO, TICK) == pytest.approx(99.5, abs=1e-9)


def test_buy_limit_result_is_always_strictly_below_bid_minus_gap() -> None:
    gap = (SYMBOL_INFO.stops_level + SYMBOL_INFO.freeze_level + 2) * SYMBOL_INFO.point
    result = normalize_limit_price(99.99, "BUY_LIMIT", SYMBOL_INFO, TICK)
    assert result is not None
    assert result < TICK.bid - gap


def test_sell_limit_result_is_always_strictly_above_ask_plus_gap() -> None:
    gap = (SYMBOL_INFO.stops_level + SYMBOL_INFO.freeze_level + 2) * SYMBOL_INFO.point
    result = normalize_limit_price(100.03, "SELL_LIMIT", SYMBOL_INFO, TICK)
    assert result is not None
    assert result > TICK.ask + gap
