from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.metrics import (
    expectancy_r,
    has_minimum_sample,
    max_drawdown_r,
    profit_factor,
    win_rate,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trade(r_multiple: float, ticket: int = 1) -> ClosedTrade:
    return ClosedTrade(
        ticket=ticket, symbol="BTCUSD", side="BUY", volume=0.01, price_open=100.0,
        price_close=100.0 + r_multiple * 5, sl=95.0, tp=120.0, magic=1, comment="grid",
        opened_at=T0, closed_at=T0, close_reason="TP" if r_multiple > 0 else "SL",
        r_multiple=r_multiple, notional_pnl=r_multiple * 5 * 0.01,
    )


def test_expectancy_r_is_the_mean_of_r_multiples() -> None:
    trades = [_trade(2.0), _trade(-1.0), _trade(1.0)]
    assert expectancy_r(trades) == pytest.approx((2.0 - 1.0 + 1.0) / 3)


def test_expectancy_r_raises_on_empty_list() -> None:
    with pytest.raises(ValueError):
        expectancy_r([])


def test_max_drawdown_r_hand_computed_example() -> None:
    # r-multiples: +2, +1, -3, -1, +4
    # cumulative:   2,  3,  0, -1,  3
    # peak:         2,  3,  3,  3,  3
    # drawdown:     0,  0,  3,  4,  0
    trades = [_trade(2.0), _trade(1.0), _trade(-3.0), _trade(-1.0), _trade(4.0)]
    assert max_drawdown_r(trades) == pytest.approx(4.0)


def test_max_drawdown_r_is_zero_when_always_at_a_new_peak() -> None:
    trades = [_trade(1.0), _trade(1.0), _trade(1.0)]
    assert max_drawdown_r(trades) == pytest.approx(0.0)


def test_max_drawdown_r_raises_on_empty_list() -> None:
    with pytest.raises(ValueError):
        max_drawdown_r([])


def test_has_minimum_sample() -> None:
    trades_29 = [_trade(1.0, ticket=i) for i in range(29)]
    trades_30 = [_trade(1.0, ticket=i) for i in range(30)]
    assert has_minimum_sample(trades_29) is False
    assert has_minimum_sample(trades_30) is True


def test_win_rate_counts_strictly_positive_r_multiples() -> None:
    trades = [_trade(2.0), _trade(-1.0), _trade(1.0), _trade(0.0)]
    assert win_rate(trades) == pytest.approx(2 / 4)


def test_win_rate_is_zero_when_no_trades_win() -> None:
    trades = [_trade(-1.0), _trade(-2.0), _trade(0.0)]
    assert win_rate(trades) == pytest.approx(0.0)


def test_win_rate_raises_on_empty_list() -> None:
    with pytest.raises(ValueError):
        win_rate([])


def test_profit_factor_hand_computed_example() -> None:
    # gross profit = 2 + 1 = 3, gross loss = 1 + 3 = 4
    trades = [_trade(2.0), _trade(-1.0), _trade(1.0), _trade(-3.0)]
    assert profit_factor(trades) == pytest.approx(3 / 4)


def test_profit_factor_is_inf_when_there_are_zero_losers() -> None:
    trades = [_trade(2.0), _trade(1.0)]
    assert profit_factor(trades) == float("inf")


def test_profit_factor_is_zero_when_all_trades_are_breakeven() -> None:
    trades = [_trade(0.0), _trade(0.0)]
    assert profit_factor(trades) == pytest.approx(0.0)


def test_profit_factor_raises_on_empty_list() -> None:
    with pytest.raises(ValueError):
        profit_factor([])
