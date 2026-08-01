from __future__ import annotations

from datetime import datetime, timezone

from mt5_mcp_trading.domain.models import Signal
from mt5_mcp_trading.trade_intent.runner import signal_to_trade_intent


def _signal(direction: str) -> Signal:
    return Signal(symbol="BTCUSD", strategy_name="runner", direction=direction,
                  timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), rationale="macd=1.23")


def test_long_becomes_buy_market_intent() -> None:
    intent = signal_to_trade_intent(_signal("LONG"))
    assert intent is not None
    assert intent.side == "BUY"
    assert intent.desired_order_type == "MARKET"
    assert intent.reference_price is None


def test_short_becomes_sell_market_intent() -> None:
    intent = signal_to_trade_intent(_signal("SHORT"))
    assert intent is not None
    assert intent.side == "SELL"
    assert intent.desired_order_type == "MARKET"


def test_flat_produces_no_intent() -> None:
    assert signal_to_trade_intent(_signal("FLAT")) is None


def test_intent_carries_the_signal_and_its_symbol_and_strategy_name() -> None:
    signal = _signal("LONG")
    intent = signal_to_trade_intent(signal)
    assert intent is not None
    assert intent.signal_ref is signal
    assert intent.symbol == signal.symbol
    assert intent.strategy_name == signal.strategy_name
    assert intent.timestamp == signal.timestamp
