"""
Real AccountReader backed by metatrader-mcp-server. Two confirmed, unresolved gaps in what
this server can honestly report -- both documented here rather than papered over, per an
explicit decision to fail safe/loud instead of quietly wrong:

1. trade_mode is UNRELIABLE. get_account_info's `account_type` field is confirmed (Phase 3,
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

2. magic numbers are NOT AVAILABLE. Confirmed via source
   (metatrader_client.utils.convert_positions_to_dataframe / convert_orders_to_dataframe):
   both use a hardcoded column mapping that omits magic (and comment) entirely, even though
   MT5's raw position/order data includes it. get_positions()/get_orders() raise
   MagicFilteringUnavailableError if a caller asks for magic-based filtering -- returning
   unfiltered data silently mislabeled as filtered would be worse than refusing, given how
   central magic-based segregation is to this project's whole risk model (grid vs runner,
   duplicate-order prevention, exposure caps). Every returned PositionState/OrderState.magic
   is set to UNKNOWN_MAGIC as a loud, greppable sentinel, not a guess.
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

UNKNOWN_MAGIC = 0
_VALID_TRADE_MODES = ("DEMO", "CONTEST", "REAL")


class MagicFilteringUnavailableError(RuntimeError):
    """Raised when magic-based filtering is requested but the underlying MCP tool doesn't
    expose magic numbers at all. See this module's docstring, gap 2."""


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
        if magic is not None:
            raise MagicFilteringUnavailableError(
                "metatrader-mcp-server exposes no magic number on positions; cannot filter "
                "by magic. See this module's docstring, gap 2."
            )
        if symbol is not None:
            raw = await self._client.call_tool("get_positions_by_symbol", {"symbol": symbol})
        else:
            raw = await self._client.call_tool("get_all_positions")

        return [
            PositionState(
                ticket=int(float(row["id"])), symbol=row["symbol"],
                side=_side_from_type(row["type"]), volume=float(row["volume"]),
                price_open=float(row["open"]), profit=float(row["profit"]),
                magic=UNKNOWN_MAGIC,
            )
            for row in parse_dataframe_csv(raw)
        ]

    async def get_orders(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[OrderState]:
        if magic is not None:
            raise MagicFilteringUnavailableError(
                "metatrader-mcp-server exposes no magic number on pending orders; cannot "
                "filter by magic. See this module's docstring, gap 2."
            )
        if symbol is not None:
            raw = await self._client.call_tool("get_pending_orders_by_symbol", {"symbol": symbol})
        else:
            raw = await self._client.call_tool("get_all_pending_orders")

        return [
            OrderState(
                ticket=int(float(row["id"])), symbol=row["symbol"],
                side=_side_from_type(row["type"]), volume=float(row["volume"]),
                price=float(row["open"]), magic=UNKNOWN_MAGIC,
            )
            for row in parse_dataframe_csv(raw)
        ]

    async def get_connection_state(self) -> ConnectionState:
        # No dedicated connection/terminal-info tool exists on this server (confirmed absent
        # in Phase 3) -- inferred from whether a read-only call succeeds at all.
        try:
            await self._client.call_tool("get_account_info")
            return ConnectionState(connected=True, detail="inferred from a successful get_account_info call")
        except Exception as exc:
            return ConnectionState(connected=False, detail=f"get_account_info failed: {exc}")
