"""
One runner evaluation cycle for one symbol -- mirrors grid_cycle.py's structure and the same
"reject/skip, never raise" policy for routine outcomes. Returns None whenever nothing was
submitted (FLAT signal, risk rejection, or a price that couldn't be planned), or the single
ExecutionResult when something was.

No duplicate-order check here, matching tests/integration/test_runner_pipeline_end_to_end.py:
that guard is about pending orders sitting at a price, and runner produces price-less MARKET
orders. account.get_orders() is still called, only to compute pending_lots for the exposure
cap, not for duplicate detection.
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
from mt5_mcp_trading.sizing.money import MoneyConfig, decide_lot, to_sized_intent
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig, runner_signal
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
) -> Optional[ExecutionResult]:
    bars = await market_data.get_bars(symbol, timeframe, bars_count)
    signal = runner_signal(bars, runner_config)
    intent = signal_to_trade_intent(signal)

    if intent is None:
        _logger.info("[RUNNER] %s FLAT, no action", symbol)
        return None

    symbol_info = await market_data.get_symbol_info(symbol)
    tick = await market_data.get_tick(symbol)
    positions = await account.get_positions(symbol=symbol, magic=magic)
    pending_orders = await account.get_orders(symbol=symbol, magic=magic)
    open_lots = sum(p.volume for p in positions)
    pending_lots = sum(o.volume for o in pending_orders)

    lot_decision = decide_lot(money_config)
    sized = to_sized_intent(intent, lot_decision)

    exposure_decision = check_exposure_cap(open_lots, pending_lots, sized.volume, caps)
    combined = combine([exposure_decision])

    if not combined.approved:
        _logger.info("[RUNNER] %s %s rejected (%s): %s",
                      symbol, intent.side, combined.blocking_guard, combined.reasons)
        return None

    plan = build_order_plan(sized, combined, symbol_info, tick, magic=magic, comment="runner")
    if plan is None:
        _logger.info("[RUNNER] %s %s price could not be planned, skipping", symbol, intent.side)
        return None

    return await executor.submit(plan)
