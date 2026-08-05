from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest

from mt5_mcp_trading.backtest.engine import (
    BacktestMarketDataSource,
    BacktestOrderExecutor,
    ReplayCursor,
    half_spread_price,
    run_backtest,
)
from mt5_mcp_trading.backtest.ledger import BacktestLedger
from mt5_mcp_trading.domain.models import MarketBar, OrderPlan, SymbolInfo
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL_INFO = SymbolInfo(symbol="BTCUSD", digits=2, point=0.01, volume_min=0.01, volume_max=100.0,
                          volume_step=0.01, stops_level=10, freeze_level=5)


def _bar(i: int, close: float, high: float, low: float, spread: int = 100) -> MarketBar:
    return MarketBar(symbol="BTCUSD", timeframe="M1", time=T0 + timedelta(minutes=i),
                      open=close, high=high, low=low, close=close, tick_volume=10, spread=spread)


def _flat_bars(n: int, price: float = 100.0) -> list[MarketBar]:
    return [_bar(i, price, price + 0.5, price - 0.5) for i in range(n)]


# ---------- ReplayCursor: the look-ahead-bias control point ----------

def test_replay_cursor_rejects_empty_bars() -> None:
    with pytest.raises(ValueError):
        ReplayCursor([])


def test_visible_bars_never_returns_anything_past_the_cursor() -> None:
    """The dedicated look-ahead test: request far more bars than exist up to the cursor, and
    confirm nothing timestamped after the cursor's own bar is ever returned."""
    bars = _flat_bars(20)
    cursor = ReplayCursor(bars)
    cursor.index = 9  # bars[10:] must never be visible

    visible = cursor.visible_bars(count=1000)  # deliberately far more than available

    assert len(visible) == 10  # only bars[0..9]
    assert visible[-1].time == bars[9].time
    assert all(b.time <= bars[9].time for b in visible)


def test_visible_bars_returns_the_last_count_bars_ending_at_the_cursor() -> None:
    bars = _flat_bars(20)
    cursor = ReplayCursor(bars)
    cursor.index = 15

    visible = cursor.visible_bars(count=5)

    assert [b.time for b in visible] == [b.time for b in bars[11:16]]


def test_visible_bars_at_index_zero_returns_only_the_first_bar() -> None:
    bars = _flat_bars(20)
    cursor = ReplayCursor(bars)
    cursor.index = 0

    assert cursor.visible_bars(count=50) == [bars[0]]


# ---------- BacktestMarketDataSource ----------

def test_market_data_source_get_tick_derives_bid_ask_from_current_bar_spread() -> None:
    bars = [_bar(0, close=100.0, high=100.5, low=99.5, spread=200)]  # 200 points * 0.01 = 2.0 price units
    cursor = ReplayCursor(bars)
    market_data = BacktestMarketDataSource(cursor, SYMBOL_INFO)

    tick = asyncio.run(market_data.get_tick("BTCUSD"))

    assert tick.ask == pytest.approx(101.0)  # close + half-spread (1.0)
    assert tick.bid == pytest.approx(99.0)   # close - half-spread


def test_market_data_source_get_bars_delegates_to_cursor() -> None:
    bars = _flat_bars(10)
    cursor = ReplayCursor(bars)
    cursor.index = 5
    market_data = BacktestMarketDataSource(cursor, SYMBOL_INFO)

    result = asyncio.run(market_data.get_bars("BTCUSD", "M1", count=3))

    assert [b.time for b in result] == [b.time for b in bars[3:6]]


# ---------- spread_multiplier (Step 4, cost/stress modeling) ----------

def test_half_spread_price_scales_linearly_with_multiplier() -> None:
    bar = _bar(0, close=100.0, high=100.5, low=99.5, spread=200)  # 200 * 0.01 / 2 = 1.0 at 1x
    assert half_spread_price(bar, SYMBOL_INFO, spread_multiplier=1.0) == pytest.approx(1.0)
    assert half_spread_price(bar, SYMBOL_INFO, spread_multiplier=2.0) == pytest.approx(2.0)
    assert half_spread_price(bar, SYMBOL_INFO, spread_multiplier=5.0) == pytest.approx(5.0)


def test_market_data_source_get_tick_honors_spread_multiplier() -> None:
    bars = [_bar(0, close=100.0, high=100.5, low=99.5, spread=200)]
    cursor = ReplayCursor(bars)
    market_data = BacktestMarketDataSource(cursor, SYMBOL_INFO, spread_multiplier=5.0)

    tick = asyncio.run(market_data.get_tick("BTCUSD"))

    assert tick.ask == pytest.approx(105.0)  # close + 5x half-spread (5.0)
    assert tick.bid == pytest.approx(95.0)


def test_market_order_fill_cost_doubles_when_spread_multiplier_doubles() -> None:
    bars = [_bar(0, close=100.0, high=100.5, low=99.5, spread=200)]

    def fill_price_at(multiplier: float) -> float:
        cursor = ReplayCursor(bars)
        ledger = BacktestLedger()
        executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO, spread_multiplier=multiplier)
        result = asyncio.run(executor.submit(_plan("MARKET", "BUY", None, sl=95.0, tp=110.0)))
        return result.executed_price

    cost_1x = fill_price_at(1.0) - 100.0
    cost_2x = fill_price_at(2.0) - 100.0

    assert cost_2x == pytest.approx(cost_1x * 2)


# ---------- BacktestOrderExecutor: fill mechanics ----------

def _plan(order_type: str, side: str, price, sl: float, tp: float, magic: int = 1) -> OrderPlan:
    return OrderPlan(symbol="BTCUSD", order_type=order_type, side=side, volume=0.01, price=price,
                      sl=sl, tp=tp, deviation=150, magic=magic, comment="test")


def test_market_order_fills_immediately_with_spread_applied() -> None:
    bars = [_bar(0, close=100.0, high=100.5, low=99.5, spread=200)]
    cursor = ReplayCursor(bars)
    ledger = BacktestLedger()
    executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO)

    result = asyncio.run(executor.submit(_plan("MARKET", "BUY", None, sl=95.0, tp=110.0)))

    assert result.success is True
    assert result.executed_price == pytest.approx(101.0)  # close + half-spread
    assert result.ticket in ledger.open_positions
    assert result.ticket not in ledger.pending_orders


def test_limit_order_becomes_pending_not_filled_immediately() -> None:
    bars = [_bar(0, close=100.0, high=100.5, low=99.5)]
    cursor = ReplayCursor(bars)
    ledger = BacktestLedger()
    executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO)

    result = asyncio.run(executor.submit(_plan("LIMIT", "BUY", 98.0, sl=95.0, tp=105.0)))

    assert result.ticket in ledger.pending_orders
    assert result.ticket not in ledger.open_positions
    assert result.executed_price is None


def test_check_fills_and_exits_fills_a_pending_order_whose_price_is_touched() -> None:
    bars = [_bar(0, close=100.0, high=100.5, low=99.5), _bar(1, close=97.0, high=99.0, low=96.5)]
    cursor = ReplayCursor(bars)
    ledger = BacktestLedger()
    executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO)
    result = asyncio.run(executor.submit(_plan("LIMIT", "BUY", 98.0, sl=95.0, tp=105.0)))

    cursor.index = 1  # bar 1's range (96.5-99.0) touches the 98.0 limit price
    executor.check_fills_and_exits(bars[1])

    assert result.ticket in ledger.open_positions
    assert result.ticket not in ledger.pending_orders
    assert ledger.open_positions[result.ticket].price_open == 98.0  # fills AT the limit price


def test_same_bar_orders_are_not_retroactively_filled_by_run_backtests_own_ordering() -> None:
    """check_fills_and_exits() is intentionally stateless/order-agnostic -- it will fill ANY
    pending order against ANY bar's range if called with that bar. The "no same-bar fill"
    guarantee (engine.py's module docstring) is therefore entirely a property of
    run_backtest()'s own loop ordering: check BEFORE that bar's cycle calls, never re-checked
    afterward for that same bar. This test demonstrates both halves directly: the order placed
    on bar 0 (whose own wide range would touch it) stays pending exactly as long as bar 0 is
    never re-checked, and DOES fill the moment bar 0 is (deliberately) checked again -- proving
    the ordering is load-bearing, not just assumed safe."""
    bar0 = _bar(0, close=100.0, high=105.0, low=90.0)  # wide range, includes 98.0
    cursor = ReplayCursor([bar0])
    ledger = BacktestLedger()
    executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO)

    cursor.index = 0
    executor.check_fills_and_exits(bar0)  # nothing pending yet -- matches run_backtest()'s pre-cycle check
    result = asyncio.run(executor.submit(_plan("LIMIT", "BUY", 98.0, sl=90.0, tp=110.0)))
    assert result.ticket in ledger.pending_orders  # correctly still pending: bar 0 was never re-checked

    executor.check_fills_and_exits(bar0)  # explicitly re-check the SAME bar -- shows what would go wrong
    assert result.ticket in ledger.open_positions  # confirms the function itself has no built-in protection


def test_sl_and_tp_both_in_range_closes_at_sl_conservative_tie_break() -> None:
    """Documented, deliberate convention (engine.py's module docstring): a same-bar double-hit
    assumes SL first."""
    bars = [_bar(0, close=100.0, high=110.0, low=90.0)]  # range contains both sl=95 and tp=105
    cursor = ReplayCursor(bars)
    ledger = BacktestLedger()
    ticket = ledger.add_open_position(symbol="BTCUSD", side="BUY", volume=0.01, price_open=100.0,
                                       sl=95.0, tp=105.0, magic=1, comment="test", opened_at=T0)
    executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO)

    executor.check_fills_and_exits(bars[0])

    assert ticket not in ledger.open_positions
    trade = ledger.closed_trades[0]
    assert trade.close_reason == "SL"
    assert trade.price_close == pytest.approx(95.0)


def test_position_does_not_exit_on_the_same_bar_it_opened() -> None:
    """Same no-look-ahead property as the pending-order case, for a freshly-opened position."""
    bars = [_bar(0, close=100.0, high=100.2, low=99.8)]
    cursor = ReplayCursor(bars)
    ledger = BacktestLedger()
    executor = BacktestOrderExecutor(cursor, ledger, SYMBOL_INFO)
    cursor.index = 0
    executor.check_fills_and_exits(bars[0])  # nothing open yet
    result = asyncio.run(executor.submit(_plan("MARKET", "BUY", None, sl=99.9, tp=100.5)))
    # bar 0's own range (99.8-100.2) would already satisfy the sl=99.9 condition, but nothing
    # re-checks bar 0 after the position opened this same bar.
    assert result.ticket in ledger.open_positions


# ---------- run_backtest(): end-to-end wiring smoke test ----------

def _synthetic_trending_bars(n: int) -> list[MarketBar]:
    """Enough bars, with real movement (a slow upward drift plus oscillation) to exercise both
    strategies' real signal math -- not hand-computed, this is a wiring/integration smoke test,
    not a precise known-answer test (those are the engine-mechanics tests above)."""
    bars = []
    price = 60000.0
    for i in range(n):
        price += 5.0 + 20.0 * math.sin(i / 7.0)
        high = price + 15.0
        low = price - 15.0
        bars.append(MarketBar(symbol="BTCUSD", timeframe="M1", time=T0 + timedelta(minutes=i),
                              open=price, high=high, low=low, close=price, tick_volume=10, spread=150))
    return bars


def test_run_backtest_completes_without_error_and_produces_a_ledger() -> None:
    bars = _synthetic_trending_bars(300)

    ledger = asyncio.run(run_backtest(
        bars=bars, symbol="BTCUSD", timeframe="M1", bars_count=100, symbol_info=SYMBOL_INFO,
        grid_config=GridStrategyConfig(atr_period=14, center_ema_period=10, step_mult=0.4),
        runner_config=RunnerStrategyConfig(),
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
        caps=ExposureCaps(max_open_lots=1.0, budget_max_lots=1.0),
        grid_magic=71101, runner_magic=72101,
    ))

    assert isinstance(ledger, BacktestLedger)
    # Something must have happened across 200 replayed cycles with a generous exposure cap --
    # either a fill, a still-pending order, or a still-open position; a completely empty ledger
    # would indicate a wiring bug, not a legitimate "no signal ever fired" outcome at this scale.
    activity = len(ledger.closed_trades) + len(ledger.open_positions) + len(ledger.pending_orders)
    assert activity > 0


def test_cycle_interval_bars_throttles_how_often_new_orders_are_evaluated() -> None:
    """A real live run found this matters, not hypothetically: the first real BTCUSD M1 backtest
    at the (then-only) every-bar cadence produced 27,234 runner trades over 50,000 bars, an
    obviously implausible rate -- traced to evaluating cycles 5x more often than the real
    bounded loop ever has (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md, Step 3). This test
    proves the throttle has a real, directional effect, not just that the parameter exists."""
    bars = _synthetic_trending_bars(300)
    common_kwargs = dict(
        bars=bars, symbol="BTCUSD", timeframe="M1", bars_count=100, symbol_info=SYMBOL_INFO,
        grid_config=GridStrategyConfig(atr_period=14, center_ema_period=10, step_mult=0.4),
        runner_config=RunnerStrategyConfig(),
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
        caps=ExposureCaps(max_open_lots=10.0, budget_max_lots=10.0),  # generous -- isolates the
        grid_magic=71101, runner_magic=72101,                        # throttle, not the cap
    )

    ledger_every_bar = asyncio.run(run_backtest(**common_kwargs, cycle_interval_bars=1))
    ledger_throttled = asyncio.run(run_backtest(**common_kwargs, cycle_interval_bars=10))

    def grid_submission_count(ledger: BacktestLedger) -> int:
        records = [*ledger.pending_orders.values(), *ledger.open_positions.values(), *ledger.closed_trades]
        return sum(1 for r in records if r.comment.startswith("grid_"))

    assert grid_submission_count(ledger_every_bar) > grid_submission_count(ledger_throttled)


def test_cycle_interval_bars_larger_than_the_run_evaluates_exactly_once() -> None:
    bars = _synthetic_trending_bars(150)  # bars_count=100 -> only 51 possible iterations
    ledger = asyncio.run(run_backtest(
        bars=bars, symbol="BTCUSD", timeframe="M1", bars_count=100, symbol_info=SYMBOL_INFO,
        grid_config=GridStrategyConfig(atr_period=14, center_ema_period=10, step_mult=0.4),
        runner_config=RunnerStrategyConfig(),
        money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
        caps=ExposureCaps(max_open_lots=10.0, budget_max_lots=10.0),
        grid_magic=71101, runner_magic=72101, cycle_interval_bars=1000,
    ))
    records = [*ledger.pending_orders.values(), *ledger.open_positions.values(), *ledger.closed_trades]
    grid_submissions = [r for r in records if r.comment.startswith("grid_")]
    assert len(grid_submissions) <= 2  # exactly one cycle ran -> at most BUY + SELL


def test_run_backtest_raises_on_invalid_cycle_interval_bars() -> None:
    bars = _flat_bars(150)
    with pytest.raises(ValueError):
        asyncio.run(run_backtest(
            bars=bars, symbol="BTCUSD", timeframe="M1", bars_count=100, symbol_info=SYMBOL_INFO,
            grid_config=GridStrategyConfig(), runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=1.0, budget_max_lots=1.0),
            grid_magic=71101, runner_magic=72101, cycle_interval_bars=0,
        ))


def test_run_backtest_raises_if_fewer_bars_than_bars_count_are_supplied() -> None:
    bars = _flat_bars(10)
    with pytest.raises(ValueError):
        asyncio.run(run_backtest(
            bars=bars, symbol="BTCUSD", timeframe="M1", bars_count=100, symbol_info=SYMBOL_INFO,
            grid_config=GridStrategyConfig(), runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=1.0, budget_max_lots=1.0),
            grid_magic=71101, runner_magic=72101,
        ))
