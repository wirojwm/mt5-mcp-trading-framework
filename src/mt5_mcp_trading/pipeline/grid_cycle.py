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

`state_store` (optional): MT5 is confirmed to always report magic=0 on every position/order
this project's own executor places, never the real intended magic (docs/mcp_tool_
classification.md item 7) -- so account.get_positions(symbol, magic=magic)/get_orders(...)'s
client-side magic filter silently matches nothing against real live data, making the exposure
cap and duplicate-order guards both no-ops (confirmed root cause, docs/
PIPELINE_WIRING_CHECKPOINT.md, post-Step-17). When a StateStore is supplied, positions/orders
are instead read unfiltered and cross-referenced against LocalOrderRecord.magic (the intended
value recorded locally at submission time, never the broker's echoed-back 0) to determine
which live tickets actually belong to this magic. When omitted (default), behavior is
unchanged from before this fix -- every mock/dry-run caller, where the magic=0 quirk doesn't
exist, is unaffected.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from mt5_mcp_trading.domain.models import ExecutionResult
from mt5_mcp_trading.market_data.interfaces import MarketDataSource
from mt5_mcp_trading.monitoring.logging_setup import get_logger
from mt5_mcp_trading.mt5_adapter.interfaces import AccountReader, OrderExecutor
from mt5_mcp_trading.order_planning.plan import build_order_plan
from mt5_mcp_trading.risk.combine import combine
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps, check_exposure_cap
from mt5_mcp_trading.risk.symbol_guards import check_duplicate_order
from mt5_mcp_trading.sizing.money import MoneyConfig, decide_lot, to_sized_intent
from mt5_mcp_trading.state.store import StateStore
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
    state_store: Optional[StateStore] = None,
) -> list[ExecutionResult]:
    bars = await market_data.get_bars(symbol, timeframe, bars_count)
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

        # SL/TP must be anchored to plan.price (the actual, broker-normalized entry
        # normalize_limit_price() decided on), NOT intent.reference_price (the pre-normalization
        # center +/- step_price level) -- live-confirmed bug (docs/PIPELINE_WIRING_CHECKPOINT.md,
        # "Step 11"): normalize_limit_price() can push the entry far enough from
        # intent.reference_price (to satisfy the broker's minimum-distance gap from the current
        # market) that an SL/TP computed from the old reference price ends up on the wrong side
        # of the real entry -- an inverted SL, rejected by the broker (or worse, silently wrong
        # if it happened to still validate).
        if intent.side == "BUY":
            sl = round(plan.price - levels.sl_price, symbol_info.digits)
            tp = round(plan.price + levels.tp_price, symbol_info.digits)
        else:
            sl = round(plan.price + levels.sl_price, symbol_info.digits)
            tp = round(plan.price - levels.tp_price, symbol_info.digits)
        plan = dataclasses.replace(plan, sl=sl, tp=tp)

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
