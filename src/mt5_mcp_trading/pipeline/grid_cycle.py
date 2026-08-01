"""
One grid evaluation cycle for one symbol: pulls bars/symbol info/tick and current
positions/pending orders, runs the full grid pipeline
(strategy -> trade_intent -> sizing -> risk -> order_planning), and submits every approved
OrderPlan to the given executor. Rejections and unnormalizable LIMIT prices are logged and
skipped, never raised -- a rejected proposal is an expected, routine outcome of one cycle, not
a failure of the cycle itself.

Every read here goes through MarketDataSource/AccountReader -- this function never calls MT5
or MCP directly, and never even imports mcp_adapter/mt5_adapter's concrete implementations,
only their interfaces (for type hints). Swap in real adapters and this code doesn't change.
"""

from __future__ import annotations

from mt5_mcp_trading.domain.models import ExecutionResult
from mt5_mcp_trading.market_data.interfaces import MarketDataSource
from mt5_mcp_trading.monitoring.logging_setup import get_logger
from mt5_mcp_trading.mt5_adapter.interfaces import AccountReader, OrderExecutor
from mt5_mcp_trading.order_planning.plan import build_order_plan
from mt5_mcp_trading.risk.combine import combine
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps, check_exposure_cap
from mt5_mcp_trading.risk.symbol_guards import check_duplicate_order
from mt5_mcp_trading.sizing.money import MoneyConfig, decide_lot, to_sized_intent
from mt5_mcp_trading.strategy.grid import GridStrategyConfig, compute_grid_levels
from mt5_mcp_trading.trade_intent.grid import grid_levels_to_trade_intents

_logger = get_logger("mt5_mcp_trading.pipeline.grid_cycle")


async def run_grid_cycle(
    market_data: MarketDataSource,
    account: AccountReader,
    executor: OrderExecutor,
    symbol: str,
    timeframe: str,
    bars_count: int,
    grid_config: GridStrategyConfig,
    money_config: MoneyConfig,
    caps: ExposureCaps,
    magic: int,
) -> list[ExecutionResult]:
    bars = await market_data.get_bars(symbol, timeframe, bars_count)
    symbol_info = await market_data.get_symbol_info(symbol)
    tick = await market_data.get_tick(symbol)

    positions = await account.get_positions(symbol=symbol, magic=magic)
    pending_orders = await account.get_orders(symbol=symbol, magic=magic)
    open_lots = sum(p.volume for p in positions)
    pending_lots = sum(o.volume for o in pending_orders)

    levels = compute_grid_levels(bars, symbol_info.point, grid_config)
    buy_intent, sell_intent = grid_levels_to_trade_intents(levels)

    results: list[ExecutionResult] = []

    for intent in (buy_intent, sell_intent):
        lot_decision = decide_lot(money_config, atr=levels.atr, step=levels.step_price)
        sized = to_sized_intent(intent, lot_decision)

        exposure_decision = check_exposure_cap(open_lots, pending_lots, sized.volume, caps)
        duplicate_decision = check_duplicate_order(
            pending_orders, intent.side, intent.reference_price, symbol_info.point
        )
        combined = combine([exposure_decision, duplicate_decision])

        if not combined.approved:
            _logger.info("[GRID] %s %s rejected (%s): %s",
                          symbol, intent.side, combined.blocking_guard, combined.reasons)
            continue

        plan = build_order_plan(sized, combined, symbol_info, tick, magic=magic,
                                 comment=f"grid_{intent.side.lower()}")
        if plan is None:
            _logger.info("[GRID] %s %s LIMIT price could not be normalized far enough "
                          "from the market, skipping", symbol, intent.side)
            continue

        results.append(await executor.submit(plan))

    return results
