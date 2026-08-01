from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.domain.models import (
    AccountState,
    MarketBar,
    OrderPlan,
    OrderState,
    PositionState,
    SymbolInfo,
    Tick,
)
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader, MockOrderExecutor
from mt5_mcp_trading.mocks.mock_market_data import MockMarketDataSource


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------- MockMarketDataSource ----------

def test_get_bars_returns_configured_bars_most_recent_last() -> None:
    bars = [
        MarketBar(symbol="BTCUSD", timeframe="M1", time=_now(), open=1, high=2, low=0, close=1.5,
                   tick_volume=1, spread=1)
        for _ in range(5)
    ]
    source = MockMarketDataSource(bars={"BTCUSD": bars})
    result = asyncio.run(source.get_bars("BTCUSD", "M1", count=3))
    assert result == bars[-3:]


def test_get_tick_raises_for_unconfigured_symbol() -> None:
    source = MockMarketDataSource()
    with pytest.raises(KeyError):
        asyncio.run(source.get_tick("EURUSD"))


def test_get_symbol_info_returns_configured_value() -> None:
    info = SymbolInfo(
        symbol="BTCUSD", digits=2, point=0.01, volume_min=0.01, volume_max=100.0,
        volume_step=0.01, stops_level=0, freeze_level=0,
    )
    source = MockMarketDataSource(symbol_infos={"BTCUSD": info})
    result = asyncio.run(source.get_symbol_info("BTCUSD"))
    assert result == info


# ---------- MockAccountReader ----------

def test_get_positions_filters_by_symbol_and_magic() -> None:
    positions = [
        PositionState(ticket=1, symbol="BTCUSD", side="BUY", volume=0.01, price_open=1.0, profit=0.0, magic=71101),
        PositionState(ticket=2, symbol="BTCUSD", side="SELL", volume=0.01, price_open=1.0, profit=0.0, magic=72101),
        PositionState(ticket=3, symbol="XAUUSD", side="BUY", volume=0.01, price_open=1.0, profit=0.0, magic=71102),
    ]
    reader = MockAccountReader(
        account_state=AccountState(login=1, server="s", balance=0, equity=0, margin_free=0, trade_mode="DEMO"),
        positions=positions,
    )
    result = asyncio.run(reader.get_positions(symbol="BTCUSD", magic=71101))
    assert [p.ticket for p in result] == [1]


def test_get_connection_state_reflects_constructor_arg() -> None:
    reader = MockAccountReader(
        account_state=AccountState(login=1, server="s", balance=0, equity=0, margin_free=0, trade_mode="DEMO"),
        connected=False,
    )
    state = asyncio.run(reader.get_connection_state())
    assert state.connected is False


# ---------- MockOrderExecutor ----------

def test_submit_records_order_plan_and_never_raises() -> None:
    executor = MockOrderExecutor()
    plan = OrderPlan(
        symbol="BTCUSD", order_type="LIMIT", side="BUY", volume=0.01, price=63000.0,
        sl=0.0, tp=0.0, deviation=150, magic=71101, comment="test",
    )
    result = asyncio.run(executor.submit(plan))
    assert result.success is True
    assert executor.submitted == [plan]


def test_cancel_and_close_are_recorded_not_sent_anywhere() -> None:
    executor = MockOrderExecutor()
    asyncio.run(executor.cancel(42))
    asyncio.run(executor.close_position(43, volume=0.01))
    assert executor.cancelled == [42]
    assert executor.closed == [(43, 0.01)]
