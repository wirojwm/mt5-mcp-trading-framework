"""
In-memory AccountReader and OrderExecutor for tests.

MockOrderExecutor never sends anything anywhere — it only records what it was asked to do, so
tests can assert on `.submitted` / `.cancelled` / `.closed` without any risk of a real call
happening, even by accident.
"""

from __future__ import annotations

from typing import Optional

from mt5_mcp_trading.domain.models import (
    AccountState,
    ConnectionState,
    ExecutionResult,
    OrderPlan,
    OrderState,
    PositionState,
)


class MockAccountReader:
    def __init__(
        self,
        account_state: AccountState,
        positions: list[PositionState] | None = None,
        orders: list[OrderState] | None = None,
        connected: bool = True,
    ) -> None:
        self._account_state = account_state
        self._positions = positions or []
        self._orders = orders or []
        self._connected = connected

    async def get_account_state(self) -> AccountState:
        return self._account_state

    async def get_positions(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[PositionState]:
        return [
            p
            for p in self._positions
            if (symbol is None or p.symbol == symbol) and (magic is None or p.magic == magic)
        ]

    async def get_orders(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[OrderState]:
        return [
            o
            for o in self._orders
            if (symbol is None or o.symbol == symbol) and (magic is None or o.magic == magic)
        ]

    async def get_connection_state(self) -> ConnectionState:
        return ConnectionState(connected=self._connected, detail="mock")


class MockOrderExecutor:
    def __init__(self) -> None:
        self.submitted: list[OrderPlan] = []
        self.cancelled: list[int] = []
        self.closed: list[tuple[int, Optional[float]]] = []

    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        self.submitted.append(order_plan)
        return ExecutionResult(
            order_plan=order_plan,
            success=True,
            retcode=0,
            ticket=1,
            broker_comment="mock",
            verified=True,
            verification_details="MockOrderExecutor - no real order was placed",
        )

    async def cancel(self, ticket: int) -> ExecutionResult:
        self.cancelled.append(ticket)
        return ExecutionResult(
            order_plan=None,
            success=True,
            retcode=0,
            ticket=ticket,
            broker_comment="mock cancel",
            verified=True,
            verification_details="MockOrderExecutor - no real cancel was sent",
        )

    async def close_position(
        self, ticket: int, volume: Optional[float] = None
    ) -> ExecutionResult:
        self.closed.append((ticket, volume))
        return ExecutionResult(
            order_plan=None,
            success=True,
            retcode=0,
            ticket=ticket,
            broker_comment="mock close",
            verified=True,
            verification_details="MockOrderExecutor - no real close was sent",
        )
