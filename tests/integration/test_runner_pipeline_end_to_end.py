"""
Proves the runner path connects end to end, mirroring
tests/integration/test_grid_pipeline_end_to_end.py:

    runner_signal -> signal_to_trade_intent -> decide_lot -> to_sized_intent
        -> check_exposure_cap -> combine -> build_order_plan

One deliberate difference from the grid pipeline test: no check_duplicate_order() here.
That guard is about *pending* orders sitting at a price; runner produces MARKET orders with
no price to be a duplicate of. Its absence here is the point -- guards are meant to be
composed per pipeline run, not applied uniformly regardless of relevance (risk/combine.py
folds together whatever list of RiskDecisions a caller passes it; a one-element list is a
legitimate use, not a shortcut).

Like the grid test, this uses real (non-mocked) pure functions throughout and does not
re-verify any single stage's own arithmetic -- runner_signal, decide_lot, the guards, and
build_order_plan all already have scratch-verified unit tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mt5_mcp_trading.domain.models import MarketBar, SymbolInfo, Tick
from mt5_mcp_trading.order_planning.plan import build_order_plan
from mt5_mcp_trading.risk.combine import combine
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps, check_exposure_cap
from mt5_mcp_trading.sizing.money import MoneyConfig, decide_lot, to_sized_intent
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig, runner_signal
from mt5_mcp_trading.trade_intent.runner import signal_to_trade_intent

from tests.unit.test_features_macd import DOWNWARD_CLOSES, UPWARD_CLOSES

MAGIC = 72101


def _bars(closes: list[float], symbol: str = "BTCUSD") -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(symbol=symbol, timeframe="M5", time=base + timedelta(minutes=5 * i),
                   open=c, high=c + 0.1, low=c - 0.1, close=c, tick_volume=1, spread=1)
        for i, c in enumerate(closes)
    ]


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(symbol="BTCUSD", digits=2, point=0.01, volume_min=0.01, volume_max=100.0,
                       volume_step=0.01, stops_level=10, freeze_level=5)


def _run_pipeline(bars, caps, magic=MAGIC):
    """Mirrors exactly what a real caller would do; returns (signal, intent, decision, plan)."""
    signal = runner_signal(bars, RunnerStrategyConfig())
    intent = signal_to_trade_intent(signal)
    if intent is None:
        return signal, None, None, None

    lot_decision = decide_lot(MoneyConfig(lot_size_mode="fixed", fixed_lot=0.02))
    sized = to_sized_intent(intent, lot_decision)

    exposure_decision = check_exposure_cap(open_lots=0.0, pending_lots=0.0,
                                            proposed_volume=sized.volume, caps=caps)
    combined = combine([exposure_decision])

    if not combined.approved:
        return signal, intent, combined, None

    tick = Tick(symbol="BTCUSD", bid=bars[-1].close - 0.02, ask=bars[-1].close, time=bars[-1].time)
    plan = build_order_plan(sized, combined, _symbol_info(), tick, magic=magic, comment="runner")
    return signal, intent, combined, plan


def test_long_signal_produces_a_buy_market_order_plan() -> None:
    caps = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)
    signal, intent, decision, plan = _run_pipeline(_bars(UPWARD_CLOSES), caps)

    assert signal.direction == "LONG"
    assert intent is not None and intent.side == "BUY"
    assert decision.approved is True
    assert plan is not None
    assert plan.order_type == "MARKET"
    assert plan.side == "BUY"
    assert plan.magic == MAGIC
    assert plan.volume == 0.02


def test_short_signal_produces_a_sell_market_order_plan() -> None:
    caps = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)
    signal, intent, decision, plan = _run_pipeline(_bars(DOWNWARD_CLOSES), caps)

    assert signal.direction == "SHORT"
    assert intent is not None and intent.side == "SELL"
    assert plan is not None
    assert plan.side == "SELL"


def test_flat_signal_short_circuits_before_sizing_or_risk_or_planning() -> None:
    caps = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)
    flat_bars = _bars([100.0] * 40)  # constant series -> macd exactly 0.0 -> FLAT
    signal, intent, decision, plan = _run_pipeline(flat_bars, caps)

    assert signal.direction == "FLAT"
    assert intent is None      # signal_to_trade_intent produced nothing
    assert decision is None    # sizing/risk never ran -- nothing to size or approve
    assert plan is None


def test_exposure_cap_blocks_the_order_plan() -> None:
    tiny_caps = ExposureCaps(max_open_lots=0.005)  # below the fixed_lot=0.02 being sized
    signal, intent, decision, plan = _run_pipeline(_bars(UPWARD_CLOSES), tiny_caps)

    assert signal.direction == "LONG"
    assert intent is not None
    assert decision.approved is False
    assert decision.blocking_guard == "portfolio.max_open_lots"
    assert plan is None  # order_planning must never have run for the rejected intent
