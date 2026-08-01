"""
MT5 account/state reads and order execution, as two deliberately separate Protocols.

AccountReader is read-only and safe to wire into any execution mode. OrderExecutor is the
only interface in this entire codebase capable of mutating broker state, and `execution/` is
the only package allowed to hold a reference to one. Nothing upstream of `execution/` ever
sees an OrderExecutor instance.
"""

from __future__ import annotations

from typing import Optional, Protocol

from mt5_mcp_trading.domain.models import (
    AccountState,
    ConnectionState,
    ExecutionResult,
    OrderPlan,
    OrderState,
    PositionState,
)


class AccountReader(Protocol):
    async def get_account_state(self) -> AccountState:
        ...

    async def get_positions(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[PositionState]:
        ...

    async def get_orders(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[OrderState]:
        ...

    async def get_connection_state(self) -> ConnectionState:
        ...


class OrderExecutor(Protocol):
    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        ...

    async def cancel(self, ticket: int) -> ExecutionResult:
        ...

    async def close_position(
        self, ticket: int, volume: Optional[float] = None
    ) -> ExecutionResult:
        ...
