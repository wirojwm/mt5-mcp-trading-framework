"""
In-memory position/pending-order/closed-trade tracker for the backtest engine (engine.py).
Shared by BacktestAccountReader (reads) and BacktestOrderExecutor (writes) so the two can never
drift out of sync -- both hold a reference to the same BacktestLedger instance.

Deliberately does not model $ balance/equity precisely: no contract-size/pip-value figure is
exposed anywhere in this codebase's domain model (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md,
"Step 3" scoping notes), so `notional_pnl` below is price-difference x volume, NOT a calibrated
dollar amount -- informational only. The authoritative metric this whole engine exists to
support is R-multiple expectancy (metrics.py), which is dimensionless and needs no such
calibration.

A position only ever closes via SL or TP in this engine (run_grid_cycle()/run_runner_cycle()
never call cancel()/close_position() themselves -- confirmed by reading both). Positions still
open when a replay run ends are reported as open, never force-closed -- force-closing at the
window's edge would bias every backtest toward whatever the last bar's price happened to be,
a well-known artifact this engine deliberately avoids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from mt5_mcp_trading.domain.models import Side


@dataclass
class OpenPosition:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float
    tp: float
    magic: int
    comment: str
    opened_at: datetime


@dataclass
class PendingOrder:
    ticket: int
    symbol: str
    side: Side  # BUY -> BUY_LIMIT, SELL -> SELL_LIMIT (grid only ever submits LIMIT orders)
    volume: float
    price: float
    sl: float
    tp: float
    magic: int
    comment: str
    placed_at: datetime


@dataclass
class ClosedTrade:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    price_close: float
    sl: float
    tp: float
    magic: int
    comment: str
    opened_at: datetime
    closed_at: datetime
    close_reason: str  # "SL" | "TP"
    r_multiple: float  # signed PnL in price units / risk-per-trade in price units
    notional_pnl: float  # price-difference x volume -- NOT calibrated to real $, see module docstring


class BacktestLedger:
    def __init__(self) -> None:
        self._next_ticket = 1
        self.open_positions: dict[int, OpenPosition] = {}
        self.pending_orders: dict[int, PendingOrder] = {}
        self.closed_trades: list[ClosedTrade] = []

    def new_ticket(self) -> int:
        ticket = self._next_ticket
        self._next_ticket += 1
        return ticket

    def add_pending_order(
        self, symbol: str, side: Side, volume: float, price: float, sl: float, tp: float,
        magic: int, comment: str, placed_at: datetime,
    ) -> int:
        ticket = self.new_ticket()
        self.pending_orders[ticket] = PendingOrder(
            ticket=ticket, symbol=symbol, side=side, volume=volume, price=price, sl=sl, tp=tp,
            magic=magic, comment=comment, placed_at=placed_at,
        )
        return ticket

    def add_open_position(
        self, symbol: str, side: Side, volume: float, price_open: float, sl: float, tp: float,
        magic: int, comment: str, opened_at: datetime, ticket: Optional[int] = None,
    ) -> int:
        """`ticket`: pass the pending order's own ticket when a LIMIT order fills (MT5
        convention this project already assumes elsewhere: a filled pending order's position
        keeps the same ticket) -- omit for a fresh MARKET fill, which gets a new ticket."""
        if ticket is None:
            ticket = self.new_ticket()
        self.open_positions[ticket] = OpenPosition(
            ticket=ticket, symbol=symbol, side=side, volume=volume, price_open=price_open,
            sl=sl, tp=tp, magic=magic, comment=comment, opened_at=opened_at,
        )
        return ticket

    def fill_pending_order(self, ticket: int, closed_at: datetime) -> None:
        """Moves a pending order into an open position at its own limit price, keeping the same
        ticket. `closed_at` here means the bar timestamp the fill was detected on, matching
        OpenPosition.opened_at's meaning (when the position actually came into existence)."""
        order = self.pending_orders.pop(ticket)
        self.add_open_position(
            symbol=order.symbol, side=order.side, volume=order.volume, price_open=order.price,
            sl=order.sl, tp=order.tp, magic=order.magic, comment=order.comment,
            opened_at=closed_at, ticket=ticket,
        )

    def close_position(self, ticket: int, price_close: float, reason: str, closed_at: datetime) -> ClosedTrade:
        position = self.open_positions.pop(ticket)
        risk = abs(position.price_open - position.sl)
        if risk <= 0:
            raise ValueError(
                f"ticket={ticket}: sl equals price_open ({position.sl}) -- risk-per-trade is "
                f"zero, an R-multiple is undefined. This should never happen given both "
                f"strategies always compute a non-zero stop distance; treat as a real bug, not "
                f"something to silently skip."
            )
        signed_pnl = (
            price_close - position.price_open if position.side == "BUY"
            else position.price_open - price_close
        )
        trade = ClosedTrade(
            ticket=ticket, symbol=position.symbol, side=position.side, volume=position.volume,
            price_open=position.price_open, price_close=price_close, sl=position.sl,
            tp=position.tp, magic=position.magic, comment=position.comment,
            opened_at=position.opened_at, closed_at=closed_at, close_reason=reason,
            r_multiple=signed_pnl / risk, notional_pnl=signed_pnl * position.volume,
        )
        self.closed_trades.append(trade)
        return trade

    def positions_for(self, symbol: Optional[str], magic: Optional[int]) -> list[OpenPosition]:
        return [
            p for p in self.open_positions.values()
            if (symbol is None or p.symbol == symbol) and (magic is None or p.magic == magic)
        ]

    def orders_for(self, symbol: Optional[str], magic: Optional[int]) -> list[PendingOrder]:
        return [
            o for o in self.pending_orders.values()
            if (symbol is None or o.symbol == symbol) and (magic is None or o.magic == magic)
        ]
