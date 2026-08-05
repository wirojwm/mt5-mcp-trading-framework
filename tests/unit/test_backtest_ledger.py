from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_mcp_trading.backtest.ledger import BacktestLedger

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_tickets_increment_and_never_repeat() -> None:
    ledger = BacktestLedger()
    a = ledger.new_ticket()
    b = ledger.new_ticket()
    assert b == a + 1


def test_add_pending_order_then_fill_keeps_the_same_ticket() -> None:
    ledger = BacktestLedger()
    ticket = ledger.add_pending_order(
        symbol="BTCUSD", side="BUY", volume=0.01, price=100.0, sl=95.0, tp=110.0,
        magic=1, comment="grid_buy", placed_at=T0,
    )
    ledger.fill_pending_order(ticket, closed_at=T0 + timedelta(minutes=1))

    assert ticket not in ledger.pending_orders
    assert ticket in ledger.open_positions
    assert ledger.open_positions[ticket].price_open == 100.0


def test_close_position_computes_r_multiple_for_a_buy() -> None:
    ledger = BacktestLedger()
    ticket = ledger.add_open_position(
        symbol="BTCUSD", side="BUY", volume=0.01, price_open=100.0, sl=95.0, tp=110.0,
        magic=1, comment="runner", opened_at=T0,
    )
    trade = ledger.close_position(ticket, price_close=110.0, reason="TP", closed_at=T0)

    # risk = |100 - 95| = 5; pnl = 110 - 100 = 10; r_multiple = 10 / 5 = 2.0
    assert trade.r_multiple == pytest.approx(2.0)
    assert trade.close_reason == "TP"
    assert trade in ledger.closed_trades
    assert ticket not in ledger.open_positions


def test_close_position_computes_r_multiple_for_a_sell() -> None:
    ledger = BacktestLedger()
    ticket = ledger.add_open_position(
        symbol="BTCUSD", side="SELL", volume=0.01, price_open=100.0, sl=105.0, tp=85.0,
        magic=1, comment="runner", opened_at=T0,
    )
    trade = ledger.close_position(ticket, price_close=85.0, reason="TP", closed_at=T0)

    # risk = |100 - 105| = 5; pnl = 100 - 85 = 15 (SELL profits on price falling); r = 15/5 = 3.0
    assert trade.r_multiple == pytest.approx(3.0)


def test_close_position_with_a_loss_gives_negative_r_multiple() -> None:
    ledger = BacktestLedger()
    ticket = ledger.add_open_position(
        symbol="BTCUSD", side="BUY", volume=0.01, price_open=100.0, sl=95.0, tp=110.0,
        magic=1, comment="runner", opened_at=T0,
    )
    trade = ledger.close_position(ticket, price_close=95.0, reason="SL", closed_at=T0)

    assert trade.r_multiple == pytest.approx(-1.0)


def test_close_position_raises_when_sl_equals_price_open() -> None:
    ledger = BacktestLedger()
    ticket = ledger.add_open_position(
        symbol="BTCUSD", side="BUY", volume=0.01, price_open=100.0, sl=100.0, tp=110.0,
        magic=1, comment="runner", opened_at=T0,
    )
    with pytest.raises(ValueError):
        ledger.close_position(ticket, price_close=105.0, reason="TP", closed_at=T0)


def test_positions_for_filters_by_symbol_and_magic() -> None:
    ledger = BacktestLedger()
    ledger.add_open_position(symbol="BTCUSD", side="BUY", volume=0.01, price_open=100.0, sl=95.0,
                              tp=110.0, magic=1, comment="grid", opened_at=T0)
    ledger.add_open_position(symbol="BTCUSD", side="BUY", volume=0.01, price_open=100.0, sl=95.0,
                              tp=110.0, magic=2, comment="runner", opened_at=T0)
    ledger.add_open_position(symbol="EURUSD", side="BUY", volume=0.01, price_open=1.1, sl=1.09,
                              tp=1.12, magic=1, comment="grid", opened_at=T0)

    assert len(ledger.positions_for(symbol="BTCUSD", magic=None)) == 2
    assert len(ledger.positions_for(symbol="BTCUSD", magic=1)) == 1
    assert len(ledger.positions_for(symbol=None, magic=1)) == 2
    assert len(ledger.positions_for(symbol=None, magic=None)) == 3


def test_orders_for_filters_by_symbol_and_magic() -> None:
    ledger = BacktestLedger()
    ledger.add_pending_order(symbol="BTCUSD", side="BUY", volume=0.01, price=100.0, sl=95.0,
                              tp=110.0, magic=1, comment="grid_buy", placed_at=T0)
    ledger.add_pending_order(symbol="BTCUSD", side="SELL", volume=0.01, price=105.0, sl=110.0,
                              tp=95.0, magic=1, comment="grid_sell", placed_at=T0)

    assert len(ledger.orders_for(symbol="BTCUSD", magic=1)) == 2
    assert len(ledger.orders_for(symbol="BTCUSD", magic=99)) == 0
