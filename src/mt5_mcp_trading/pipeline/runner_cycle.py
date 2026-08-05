"""
One runner evaluation cycle for one symbol -- mirrors grid_cycle.py's structure and the same
"reject/skip, never raise" policy for routine outcomes. Returns None whenever nothing was
submitted (FLAT signal, risk rejection, or a price that couldn't be planned), or the single
ExecutionResult when something was.

No duplicate-order check here, matching tests/integration/test_runner_pipeline_end_to_end.py:
that guard is about pending orders sitting at a price, and runner produces price-less MARKET
orders. account.get_orders() is still called, only to compute pending_lots for the exposure
cap, not for duplicate detection.

check_position_limit() (risk/symbol_guards.py) IS new here -- added to fix a real, previously-
invisible gap (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md, "Fix runner's re-entry throttle"):
without it, this function had no protection against re-entering the same direction repeatedly
beyond the raw lot-based exposure cap, so every cycle where the signal stayed directional and
any exposure "slot" was free, it opened another position. A 10,000-cycle backtest against real
BTCUSD M1 history produced 9,881 trades and -0.159R expectancy before this fix, traced to
exactly this. RunnerStrategyConfig.max_concurrent_positions (default 1) controls it -- open to
tuning like every other field there.

`state_store` (optional): see grid_cycle.py's module docstring for the full explanation --
same magic=0 quirk, same fix. When supplied, positions/orders are read unfiltered and
cross-referenced against LocalOrderRecord.magic instead of trusting the broker's client-side
magic filter. When omitted (default), behavior is unchanged from before this fix.
"""

from __future__ import annotations

from typing import Optional

from mt5_mcp_trading.domain.models import ExecutionResult
from mt5_mcp_trading.market_data.interfaces import MarketDataSource
from mt5_mcp_trading.monitoring.logging_setup import get_logger
from mt5_mcp_trading.mt5_adapter.interfaces import AccountReader, OrderExecutor
from mt5_mcp_trading.order_planning.plan import build_order_plan
from mt5_mcp_trading.risk.combine import combine
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps, check_exposure_cap
from mt5_mcp_trading.risk.symbol_guards import check_position_limit
from mt5_mcp_trading.sizing.money import MoneyConfig, decide_lot, to_sized_intent
from mt5_mcp_trading.state.store import StateStore
from mt5_mcp_trading.strategy.runner import (
    RunnerStrategyConfig,
    compute_stop_distances,
    runner_signal,
)
from mt5_mcp_trading.trade_intent.runner import signal_to_trade_intent

_logger = get_logger("mt5_mcp_trading.pipeline.runner_cycle")


async def run_runner_cycle(
    market_data: MarketDataSource,
    account: AccountReader,
    executor: OrderExecutor,
    symbol: str,
    timeframe: str,
    bars_count: int,
    runner_config: RunnerStrategyConfig,
    money_config: MoneyConfig,
    caps: ExposureCaps,
    magic: int,
    state_store: Optional[StateStore] = None,
) -> Optional[ExecutionResult]:
    bars = await market_data.get_bars(symbol, timeframe, bars_count)
    signal = runner_signal(bars, runner_config)
    intent = signal_to_trade_intent(signal)

    if intent is None:
        _logger.info("[RUNNER] %s FLAT, no action", symbol)
        return None

    symbol_info = await market_data.get_symbol_info(symbol)
    tick = await market_data.get_tick(symbol)

    if state_store is None:
        positions = await account.get_positions(symbol=symbol, magic=magic)
        pending_orders = await account.get_orders(symbol=symbol, magic=magic)
    else:
        local_tickets = {r.ticket for r in state_store.all_open() if r.magic == magic}
        all_positions = await account.get_positions(symbol=symbol)
        all_orders = await account.get_orders(symbol=symbol)
        positions = [p for p in all_positions if p.ticket in local_tickets]
        pending_orders = [o for o in all_orders if o.ticket in local_tickets]

    open_lots = sum(p.volume for p in positions)
    pending_lots = sum(o.volume for o in pending_orders)

    lot_decision = decide_lot(money_config)
    sized = to_sized_intent(intent, lot_decision)

    exposure_decision = check_exposure_cap(open_lots, pending_lots, sized.volume, caps)
    position_limit_decision = check_position_limit(positions, runner_config.max_concurrent_positions)
    combined = combine([exposure_decision, position_limit_decision])

    if not combined.approved:
        _logger.info("[RUNNER] %s %s rejected (%s): %s",
                      symbol, intent.side, combined.blocking_guard, combined.reasons)
        return None

    sl_distance, tp_distance = compute_stop_distances(bars, symbol_info.point, runner_config)
    # intent.reference_price is always None for runner (trade_intent/runner.py) -- this mirrors
    # build_order_plan()'s own MARKET fallback (order_planning/plan.py) for the same reason, so
    # build_order_plan resolves the identical price it would have anyway.
    reference_price = tick.ask if intent.side == "BUY" else tick.bid
    if intent.side == "BUY":
        sl = round(reference_price - sl_distance, symbol_info.digits)
        tp = round(reference_price + tp_distance, symbol_info.digits)
    else:
        sl = round(reference_price + sl_distance, symbol_info.digits)
        tp = round(reference_price - tp_distance, symbol_info.digits)

    plan = build_order_plan(sized, combined, symbol_info, tick, magic=magic, comment="runner",
                             sl=sl, tp=tp)
    if plan is None:
        _logger.info("[RUNNER] %s %s price could not be planned, skipping", symbol, intent.side)
        return None

    return await executor.submit(plan)
