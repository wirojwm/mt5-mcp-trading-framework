"""
EMA-long expected value cross-checked against the legacy adjust=False EMA recursion in a
standalone scratch script (over the 31 closes preceding the currently-forming bar) -- not
derived from this implementation itself or from importing the legacy code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_mcp_trading.domain.models import GuardState, MarketBar, PositionState
from mt5_mcp_trading.strategy.guard import GuardConfig, evaluate_guard

CLOSES = [
    100.0, 99.9714277321059, 100.00729116375419, 100.26181751416843, 100.24120755622829,
    100.24591232006566, 100.29834321737559, 100.10913942368852, 100.1162846071136,
    100.19421423924368, 100.37000036275565, 100.12647443649318, 100.0085151940679,
    99.76291751656301, 99.9487042371833, 100.06476732670805, 99.78989552852995,
    100.07921158100922, 100.35806624968455, 100.45041976980485, 100.519757392552,
    100.31425384963609, 100.02325429180586, 100.04028305150814, 99.77601371460945,
    99.5901386722882, 99.43530448048733, 99.1533540340222, 99.13171471075617,
    99.09603338075017, 99.3014896578613, 99.31296412671975,
]
EMA_LONG_AT_LAST_CLOSED = 99.79382721147729  # ema(CLOSES[:-1], 26)
MAGIC = 71101


def _bars(last_closed_high: float, last_closed_low: float, symbol: str = "BTCUSD") -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i, c in enumerate(CLOSES):
        t = base + timedelta(minutes=5 * i)
        if i == len(CLOSES) - 2:  # the last CLOSED bar (bars[-2] once the list is complete)
            h, lo = last_closed_high, last_closed_low
        else:
            h, lo = c + 0.05, c - 0.05
        bars.append(MarketBar(symbol=symbol, timeframe="M5", time=t, open=c, high=h, low=lo,
                               close=c, tick_volume=1, spread=1))
    return bars


def _positions(side: str, magic: int = MAGIC) -> list[PositionState]:
    return [PositionState(ticket=1, symbol="BTCUSD", side=side, volume=0.02, price_open=100.0,
                           profit=0.0, magic=magic)]


def test_long_trigger_partial_closes_and_sets_deadline() -> None:
    bars = _bars(last_closed_high=99.5, last_closed_low=99.2)  # high (99.5) < ema (99.79)
    config = GuardConfig(ema_long=26, partial_ratio_long=0.5, deadline_bars=6)
    new_state, action = evaluate_guard(bars, _positions("BUY"), MAGIC, GuardState(), config)

    assert action.kind == "PARTIAL_CLOSE"
    assert action.ratio == 0.5
    assert new_state.deadline_bars_remaining == 6
    assert new_state.last_evaluated_bar_time == bars[-2].time


def test_short_trigger_partial_closes_and_sets_deadline() -> None:
    bars = _bars(last_closed_high=100.1, last_closed_low=100.0)  # low (100.0) > ema (99.79)
    config = GuardConfig(ema_long=26, partial_ratio_short=0.7, deadline_bars=6)
    new_state, action = evaluate_guard(bars, _positions("SELL"), MAGIC, GuardState(), config)

    assert action.kind == "PARTIAL_CLOSE"
    assert action.ratio == 0.7
    assert new_state.deadline_bars_remaining == 6


def test_no_trigger_when_bar_brackets_ema_long() -> None:
    bars = _bars(last_closed_high=99.85, last_closed_low=99.20)  # brackets ema (99.79)
    config = GuardConfig(ema_long=26)
    new_state, action = evaluate_guard(bars, _positions("BUY"), MAGIC, GuardState(), config)

    assert action.kind == "NONE"
    assert new_state.deadline_bars_remaining == 0


def test_no_trigger_without_matching_position_even_if_price_condition_holds() -> None:
    bars = _bars(last_closed_high=99.5, last_closed_low=99.2)  # would trigger LONG if held
    config = GuardConfig(ema_long=26)
    new_state, action = evaluate_guard(bars, [], MAGIC, GuardState(), config)  # no positions
    assert action.kind == "NONE"


def test_no_trigger_when_position_magic_does_not_match() -> None:
    bars = _bars(last_closed_high=99.5, last_closed_low=99.2)
    config = GuardConfig(ema_long=26)
    other_magic_position = _positions("BUY", magic=99999)
    new_state, action = evaluate_guard(bars, other_magic_position, MAGIC, GuardState(), config)
    assert action.kind == "NONE"


def test_partial_close_ratio_applies_regardless_of_which_side_triggered() -> None:
    # Faithful to the legacy _partial_close(): it filters positions by magic only, not side.
    # A long-side trigger's ratio is still meant to apply to whatever's under that magic --
    # this test just confirms the action doesn't encode a side, only a ratio.
    bars = _bars(last_closed_high=99.5, last_closed_low=99.2)
    config = GuardConfig(ema_long=26, partial_ratio_long=0.3)
    _, action = evaluate_guard(bars, _positions("BUY"), MAGIC, GuardState(), config)
    assert action.ratio == 0.3
    assert not hasattr(action, "side")


def test_deadline_counts_down_and_flattens_at_zero_across_successive_new_bars() -> None:
    config = GuardConfig(ema_long=26, deadline_bars=2)
    neutral_bars = _bars(last_closed_high=99.85, last_closed_low=99.20)

    state = GuardState(deadline_bars_remaining=2, last_evaluated_bar_time=None)
    # First new bar: no trigger, deadline 2 -> 1
    state, action1 = evaluate_guard(neutral_bars, _positions("BUY"), MAGIC, state, config)
    assert action1.kind == "NONE"
    assert state.deadline_bars_remaining == 1

    # Simulate a second, later bar (advance last_evaluated_bar_time back so this "looks new")
    later_bars = _bars(last_closed_high=99.85, last_closed_low=99.20)
    later_bars = [
        b if i != len(later_bars) - 2 else
        MarketBar(symbol=b.symbol, timeframe=b.timeframe, time=b.time + timedelta(minutes=5),
                   open=b.open, high=b.high, low=b.low, close=b.close,
                   tick_volume=b.tick_volume, spread=b.spread)
        for i, b in enumerate(later_bars)
    ]
    state, action2 = evaluate_guard(later_bars, _positions("BUY"), MAGIC, state, config)
    assert action2.kind == "FLATTEN"
    assert state.deadline_bars_remaining == 0


def test_same_closed_bar_evaluated_twice_does_not_reduce_deadline_or_retrigger() -> None:
    # Regression test for bug 1 (missing bar-level dedup): calling evaluate_guard twice with
    # the identical bars list (same last_evaluated_bar_time) must be a no-op the second time,
    # not a repeated trigger / repeated deadline decrement.
    bars = _bars(last_closed_high=99.5, last_closed_low=99.2)  # triggers LONG
    config = GuardConfig(ema_long=26, deadline_bars=6)

    state1, action1 = evaluate_guard(bars, _positions("BUY"), MAGIC, GuardState(), config)
    assert action1.kind == "PARTIAL_CLOSE"
    assert state1.deadline_bars_remaining == 6

    # Evaluate again with the SAME bars (same last-closed bar time) and the state carried over.
    state2, action2 = evaluate_guard(bars, _positions("BUY"), MAGIC, state1, config)
    assert action2.kind == "NONE"
    assert state2 == state1  # nothing changed -- no re-trigger, no deadline decrement


def test_raises_below_ema_long_plus_5_bars() -> None:
    config = GuardConfig(ema_long=26)
    short_bars = _bars(last_closed_high=99.5, last_closed_low=99.2)[:30]  # < 26+5=31
    with pytest.raises(ValueError):
        evaluate_guard(short_bars, _positions("BUY"), MAGIC, GuardState(), config)


def test_ema_long_value_matches_scratch_verified_expectation() -> None:
    # Indirect check that the EMA computation wired into evaluate_guard uses closes through
    # the last CLOSED bar only (bars[:-1]), matching the scratch-computed value: a bar whose
    # high sits exactly at that boundary should trigger on one side of it and not the other.
    just_below = _bars(last_closed_high=EMA_LONG_AT_LAST_CLOSED - 0.001, last_closed_low=99.0)
    just_above = _bars(last_closed_high=EMA_LONG_AT_LAST_CLOSED + 0.001, last_closed_low=99.0)
    config = GuardConfig(ema_long=26)

    _, action_below = evaluate_guard(just_below, _positions("BUY"), MAGIC, GuardState(), config)
    _, action_above = evaluate_guard(just_above, _positions("BUY"), MAGIC, GuardState(), config)

    assert action_below.kind == "PARTIAL_CLOSE"
    assert action_above.kind == "NONE"
