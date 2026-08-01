"""
DRY_RUN mode's OrderExecutor: implements the interface but never calls anything real. Matches
the Phase 1 design for DRY_RUN mode exactly -- "OrderPlan fully built and logged; submit()
never calls MCP, returns a synthetic ExecutionResult".

Deliberately built on top of mocks.MockOrderExecutor rather than reimplementing the same
record-only behavior a second time: MockOrderExecutor already IS exactly what "never touch
anything real, just record what was asked" means, and that mechanical behavior is identical
for both MOCK and DRY_RUN modes. The difference between the two modes isn't in what the
executor does -- it's in what feeds it (MOCK mode also fakes market data; DRY_RUN mode is
meant to run against real market data once the real MarketDataSource exists, with only
execution intercepted) and in DRY_RUN being a real, documented, first-class system behavior
that a human operator watches, not test scaffolding. That's what the logging here is for.
"""

from __future__ import annotations

import logging
from typing import Optional

from mt5_mcp_trading.domain.models import ExecutionResult, OrderPlan
from mt5_mcp_trading.mocks.mock_account_and_executor import MockOrderExecutor
from mt5_mcp_trading.monitoring.logging_setup import get_logger


class DryRunExecutor:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._delegate = MockOrderExecutor()
        self._logger = logger or get_logger("mt5_mcp_trading.execution.dry_run")

    @property
    def submitted(self) -> list[OrderPlan]:
        return self._delegate.submitted

    @property
    def cancelled(self) -> list[int]:
        return self._delegate.cancelled

    @property
    def closed(self) -> list[tuple[int, Optional[float]]]:
        return self._delegate.closed

    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        self._logger.info(
            "[DRY-RUN] would submit: %s %s %s %s lots @ %s (magic=%s, comment=%r)",
            order_plan.symbol, order_plan.order_type, order_plan.side,
            order_plan.volume, order_plan.price, order_plan.magic, order_plan.comment,
        )
        return await self._delegate.submit(order_plan)

    async def cancel(self, ticket: int) -> ExecutionResult:
        self._logger.info("[DRY-RUN] would cancel order ticket=%s", ticket)
        return await self._delegate.cancel(ticket)

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        self._logger.info("[DRY-RUN] would close position ticket=%s volume=%s", ticket, volume)
        return await self._delegate.close_position(ticket, volume)
