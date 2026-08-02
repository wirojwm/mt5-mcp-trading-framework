"""
Real AccountReader backed by metatrader-mcp-server (extended locally with two tools -- see
scripts/metatrader_mcp_extended_server.py's module docstring for why). One confirmed,
unresolved gap in what this server can honestly report, documented here rather than papered
over, per an explicit decision to fail safe/loud instead of quietly wrong:

trade_mode is UNRELIABLE. get_account_info's `account_type` field is confirmed (Phase 3,
cross-checked against the raw MetaTrader5.account_info().trade_mode integer on a live
connection) to be inverted relative to MT5's real ACCOUNT_TRADE_MODE_* enum: the server
reports "real" for what MT5 itself calls DEMO (trade_mode=0), "demo" for CONTEST (1), and
"contest" for REAL (2).

This adapter does NOT apply the inversion-correction, even though the exact mapping is
known precisely. Correcting it would be a bet that metatrader-mcp-server's bug stays
exactly as observed forever; if that upstream package is ever fixed, a "correction" here
would silently start reporting the dangerous direction -- a genuinely REAL account
reported as DEMO -- with nothing in this codebase able to detect that it happened. Passed
through as-is (just uppercased) instead: this fails toward refusing to proceed (a real
account never reports as "DEMO" under either the buggy or a hypothetically-fixed mapping
in the direction that matters), at the cost of a genuine demo account sometimes not being
recognized as one. See require_demo_account() in mt5_adapter/safety.py -- when fed a
McpAccountReader's output, its correctness has never depended on this field being right;
the actual safety boundary for this project is the MT5_ACCOUNT_KIND=DEMO pre-launch gate
in scripts/run_metatrader_mcp_stdio.py, confirmed independently of anything read over MCP.

Previously, magic numbers were NOT AVAILABLE at all: metatrader-mcp-server's own
get_all_positions/get_positions_by_symbol/get_all_pending_orders/get_pending_orders_by_symbol
tools drop the field entirely (confirmed via source -- metatrader_client.utils.
convert_positions_to_dataframe/convert_orders_to_dataframe use a hardcoded column mapping that
omits magic and comment, even though MT5's raw position/order data includes both). Resolved
by adding get_positions_with_magic/get_pending_orders_with_magic locally (see
scripts/metatrader_mcp_extended_server.py) -- get_positions()/get_orders() below now call
those and filter by magic for real, client-side, rather than raising.
"""

from __future__ import annotations

import json
from typing import Optional

from mt5_mcp_trading.domain.models import (
    AccountState,
    ConnectionState,
    OrderState,
    PositionState,
    TradeMode,
)
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.mt5_adapter.metatrader_parsing import parse_dataframe_csv

_VALID_TRADE_MODES = ("DEMO", "CONTEST", "REAL")


def _side_from_type(raw_type: str) -> str:
    return "BUY" if raw_type.strip().upper().startswith("BUY") else "SELL"


class McpAccountReader:
    def __init__(self, client: McpClient) -> None:
        self._client = client

    async def get_account_state(self) -> AccountState:
        raw = await self._client.call_tool("get_account_info")
        data = json.loads(raw)

        reported = str(data.get("account_type", "")).strip().upper()
        trade_mode: TradeMode = reported if reported in _VALID_TRADE_MODES else "REAL"  # type: ignore[assignment]

        return AccountState(
            login=None,  # not exposed by any tool on this server -- see AccountState's docstring
            server=None,
            balance=float(data["balance"]),
            equity=float(data["equity"]),
            margin_free=float(data["free_margin"]),
            trade_mode=trade_mode,
        )

    async def get_positions(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[PositionState]:
        args = {"symbol_name": symbol} if symbol is not None else None
        raw = await self._client.call_tool("get_positions_with_magic", args)

        positions = [
            PositionState(
                ticket=int(float(row["id"])), symbol=row["symbol"],
                side=_side_from_type(row["type"]), volume=float(row["volume"]),
                price_open=float(row["open"]), profit=float(row["profit"]),
                magic=int(float(row["magic"])),
            )
            for row in parse_dataframe_csv(raw)
        ]
        if magic is not None:
            positions = [p for p in positions if p.magic == magic]
        return positions

    async def get_orders(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[OrderState]:
        args = {"symbol_name": symbol} if symbol is not None else None
        raw = await self._client.call_tool("get_pending_orders_with_magic", args)

        orders = [
            OrderState(
                ticket=int(float(row["id"])), symbol=row["symbol"],
                side=_side_from_type(row["type"]), volume=float(row["volume"]),
                price=float(row["open"]), magic=int(float(row["magic"])),
            )
            for row in parse_dataframe_csv(raw)
        ]
        if magic is not None:
            orders = [o for o in orders if o.magic == magic]
        return orders

    async def get_connection_state(self) -> ConnectionState:
        # No dedicated connection/terminal-info tool exists on this server (confirmed absent
        # in Phase 3) -- inferred from whether a read-only call succeeds at all.
        try:
            await self._client.call_tool("get_account_info")
            return ConnectionState(connected=True, detail="inferred from a successful get_account_info call")
        except Exception as exc:
            return ConnectionState(connected=False, detail=f"get_account_info failed: {exc}")
