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

Phase 7 (regression and failure testing) design decision: BUY and SELL are logically
independent proposals (different prices, different tickets once submitted) -- one side's
executor.submit() raising must never silently prevent the other, healthy side from being
attempted, and must never silently discard a result already obtained for the other side.
Before this, a raise on the second side propagated immediately, losing the first side's
already-appended ExecutionResult from ever reaching the caller (the real MT5/local-state
record was never lost -- McpOrderExecutor persists before returning -- only this function's
in-memory return value was). Fixed by catching per side, always attempting both regardless of
either failing, and raising a single GridCycleError at the end if anything failed -- carrying
every ExecutionResult that WAS obtained plus every exception that occurred, so nothing
observed during the cycle is ever silently lost, while still making failure impossible to
ignore (the function still raises, never returns a falsely-clean empty/partial list).
Read-stage failures (get_bars/get_symbol_info/get_tick/get_positions/get_orders, all before
the per-side loop) are NOT wrapped -- there's nothing to preserve yet at that point, so the
raw exception propagates unchanged.
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


class GridCycleError(RuntimeError):
    """Raised when one or both sides' executor.submit() call raises during run_grid_cycle().
    The other side is still attempted regardless -- BUY/SELL are logically independent, so one
    side's failure must never prevent the other from being submitted. Carries every
    ExecutionResult that WAS successfully obtained (.completed_results) and every per-side
    exception (.errors, as (side, exception) pairs) -- nothing observed during the cycle is
    lost just because something else in the same cycle failed."""

    def __init__(
        self, completed_results: list[ExecutionResult], errors: list[tuple[str, Exception]]
    ) -> None:
        self.completed_results = completed_results
        self.errors = errors
        sides = ", ".join(f"{side}: {exc!r}" for side, exc in errors)
        super().__init__(
            f"run_grid_cycle: {len(errors)} side(s) raised during executor.submit() ({sides}); "
            f"{len(completed_results)} side(s) completed successfully -- see "
            f".completed_results/.errors, nothing was silently lost."
        )


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
    errors: list[tuple[str, Exception]] = []

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

        try:
            results.append(await executor.submit(plan))
        except Exception as exc:
            _logger.exception(
                "[GRID] %s %s executor.submit() raised -- other side still attempted",
                symbol, intent.side,
            )
            errors.append((intent.side, exc))

    if errors:
        raise GridCycleError(results, errors)

    return results
