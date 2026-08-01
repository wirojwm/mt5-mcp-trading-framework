"""
Proves the grid path actually connects, end to end, through real (not mocked) pure functions:

    compute_grid_levels -> grid_levels_to_trade_intents -> decide_lot -> to_sized_intent
        -> check_exposure_cap + check_duplicate_order -> combine -> build_order_plan

No adapters, no mocks, no MT5/MCP connection -- every stage here is a pure function operating
on plain data, which is itself the thing being demonstrated: this whole chain is executable
and testable without a live terminal.

This is deliberately not re-proving each stage's own arithmetic (compute_grid_levels,
decide_lot, the guards, and build_order_plan all already have scratch-verified unit tests) --
its job is to prove the pieces fit together and that a rejection at the risk stage correctly
prevents order_planning from ever running for that intent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mt5_mcp_trading.domain.models import MarketBar, OrderState, SymbolInfo, Tick
from mt5_mcp_trading.order_planning.plan import build_order_plan
from mt5_mcp_trading.risk.combine import combine
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps, check_exposure_cap
from mt5_mcp_trading.risk.symbol_guards import check_duplicate_order
from mt5_mcp_trading.sizing.money import MoneyConfig, decide_lot, to_sized_intent
from mt5_mcp_trading.strategy.grid import GridStrategyConfig, compute_grid_levels
from mt5_mcp_trading.trade_intent.grid import grid_levels_to_trade_intents

MAGIC = 71101
POINT = 0.01
PRICES = [
    63000, 63010, 62995, 63020, 63005, 62990, 63015, 63030, 63010, 62998,
    63022, 63040, 63018, 63005, 62995, 63012,
]


def _bars() -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketBar(symbol="BTCUSD", timeframe="M1", time=base + timedelta(minutes=i),
                   open=p, high=p + 8, low=p - 8, close=p, tick_volume=10, spread=2)
        for i, p in enumerate(PRICES)
    ]


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(symbol="BTCUSD", digits=2, point=POINT, volume_min=0.01, volume_max=100.0,
                       volume_step=0.01, stops_level=10, freeze_level=5)


def _grid_config() -> GridStrategyConfig:
    return GridStrategyConfig(atr_period=14, center_ema_period=10, step_mult=0.4)


def _money_config() -> MoneyConfig:
    return MoneyConfig(lot_size_mode="atr_scale", atr_scale_base=0.01, atr_scale_ref=1.0,
                        atr_scale_min=0.01, atr_scale_max=0.06)


def _plan_one_side(intent, levels, tick, symbol_info, existing_orders, caps):
    """Runs one TradeIntent through sizing -> risk -> order_planning, mirroring exactly what
    a real caller would do. Returns (sized_intent, combined_decision, order_plan_or_none)."""
    lot_decision = decide_lot(_money_config(), atr=levels.atr, step=levels.step_price)
    sized = to_sized_intent(intent, lot_decision)

    exposure_decision = check_exposure_cap(open_lots=0.0, pending_lots=0.0,
                                            proposed_volume=sized.volume, caps=caps)
    duplicate_decision = check_duplicate_order(existing_orders, intent.side,
                                                intent.reference_price, point=POINT)
    combined = combine([exposure_decision, duplicate_decision])

    if not combined.approved:
        return sized, combined, None

    plan = build_order_plan(sized, combined, symbol_info, tick, magic=MAGIC,
                             comment=f"grid_{intent.side.lower()}")
    return sized, combined, plan


def test_happy_path_both_sides_produce_valid_order_plans() -> None:
    bars = _bars()
    levels = compute_grid_levels(bars, point=POINT, config=_grid_config())
    buy_intent, sell_intent = grid_levels_to_trade_intents(levels)

    assert buy_intent.reference_price < levels.center < sell_intent.reference_price

    symbol_info = _symbol_info()
    tick = Tick(symbol="BTCUSD", bid=levels.center - 1.0, ask=levels.center + 1.0, time=bars[-1].time)
    caps = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)

    _, buy_decision, buy_plan = _plan_one_side(buy_intent, levels, tick, symbol_info, [], caps)
    _, sell_decision, sell_plan = _plan_one_side(sell_intent, levels, tick, symbol_info, [], caps)

    assert buy_decision.approved is True
    assert sell_decision.approved is True

    assert buy_plan is not None
    assert buy_plan.side == "BUY"
    assert buy_plan.order_type == "LIMIT"
    assert buy_plan.symbol == "BTCUSD"
    assert buy_plan.magic == MAGIC
    assert buy_plan.volume > 0
    assert buy_plan.price < tick.bid  # a valid BUY_LIMIT must sit below bid

    assert sell_plan is not None
    assert sell_plan.side == "SELL"
    assert sell_plan.price > tick.ask  # a valid SELL_LIMIT must sit above ask


def test_duplicate_order_guard_blocks_only_the_matching_side() -> None:
    bars = _bars()
    levels = compute_grid_levels(bars, point=POINT, config=_grid_config())
    buy_intent, sell_intent = grid_levels_to_trade_intents(levels)

    symbol_info = _symbol_info()
    tick = Tick(symbol="BTCUSD", bid=levels.center - 1.0, ask=levels.center + 1.0, time=bars[-1].time)
    caps = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)

    # A pending BUY already sits right at the grid's proposed buy_price -- must block the BUY
    # intent (duplicate) while leaving the SELL intent, on the opposite side, untouched.
    existing = [OrderState(ticket=999, symbol="BTCUSD", side="BUY",
                            volume=0.01, price=buy_intent.reference_price, magic=MAGIC)]

    _, buy_decision, buy_plan = _plan_one_side(buy_intent, levels, tick, symbol_info, existing, caps)
    _, sell_decision, sell_plan = _plan_one_side(sell_intent, levels, tick, symbol_info, existing, caps)

    assert buy_decision.approved is False
    assert buy_decision.blocking_guard == "symbol.duplicate_order"
    assert buy_plan is None  # order_planning must never have run for the rejected intent

    assert sell_decision.approved is True
    assert sell_plan is not None


def test_exposure_cap_blocks_both_sides_when_already_effectively_full() -> None:
    bars = _bars()
    levels = compute_grid_levels(bars, point=POINT, config=_grid_config())
    buy_intent, sell_intent = grid_levels_to_trade_intents(levels)

    symbol_info = _symbol_info()
    tick = Tick(symbol="BTCUSD", bid=levels.center - 1.0, ask=levels.center + 1.0, time=bars[-1].time)
    # Cap tighter than any single proposed volume -- both sides must be rejected.
    tiny_caps = ExposureCaps(max_open_lots=0.005)

    _, buy_decision, buy_plan = _plan_one_side(buy_intent, levels, tick, symbol_info, [], tiny_caps)
    _, sell_decision, sell_plan = _plan_one_side(sell_intent, levels, tick, symbol_info, [], tiny_caps)

    assert buy_decision.approved is False
    assert buy_decision.blocking_guard == "portfolio.max_open_lots"
    assert buy_plan is None

    assert sell_decision.approved is False
    assert sell_plan is None
