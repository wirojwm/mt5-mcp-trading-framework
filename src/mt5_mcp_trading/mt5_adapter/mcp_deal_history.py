"""
Real deal-history reader backed by metatrader-mcp-server's get_deals tool (already classified
READ_ONLY in mcp_adapter/metatrader_tools.py, no local extension needed -- unlike
get_positions_with_magic/get_pending_orders_with_magic, this tool already exists upstream).

get_deals returns the same "serialized pandas DataFrame" CSV shape as every other history/
market-data tool (see metatrader_parsing.py's module docstring) -- parse_dataframe_csv() is
reused as-is, per Phase 9 Step 4's research.

Two wire-format quirks confirmed by tracing past the tool into the vendored
metatrader_client.history package (not guessed -- see domain/models.py's Deal docstring for
the full trace):

- `type` is a raw MT5 ENUM_DEAL_TYPE int (e.g. 0=BUY, 1=SELL, 2=BALANCE...), never a string.
- `time` is a genuine UTC instant (converted from MT5's epoch-seconds field) but arrives with
  no offset suffix at all -- unlike candles/positions/orders/price, which all carry an
  explicit "Z" or "+00:00" (see metatrader_parsing.py). parse_iso_datetime() would silently
  return a naive datetime here, so this module attaches tzinfo=timezone.utc explicitly rather
  than trust the string to say so.

magic is deliberately parsed and carried onto the Deal (the raw wire value is not thrown
away), but per Deal's own docstring it must never be used by a caller for strategy
attribution -- that's StateStore's job, matched by position_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from mt5_mcp_trading.domain.models import Deal
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.mt5_adapter.metatrader_parsing import parse_dataframe_csv, parse_iso_datetime


def _parse_deal_time(raw: str) -> datetime:
    parsed = parse_iso_datetime(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class McpDealHistoryReader:
    def __init__(self, client: McpClient) -> None:
        self._client = client

    async def get_deals(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> list[Deal]:
        """from_date/to_date: "YYYY-MM-DD" per the tool's own docstring. symbol maps to the
        tool's "symbol" argument (server-side group-filters by it, see
        metatrader_mcp/server.py); None means no filter, matching every other reader in this
        package. Only non-None arguments are sent -- same conditional-args convention as
        McpAccountReader.get_positions()/get_orders(), rather than passing explicit nulls."""
        args: dict[str, Any] = {}
        if from_date is not None:
            args["from_date"] = from_date
        if to_date is not None:
            args["to_date"] = to_date
        if symbol is not None:
            args["symbol"] = symbol
        raw = await self._client.call_tool("get_deals", args if args else None)
        return [
            Deal(
                ticket=int(float(row["ticket"])), order=int(float(row["order"])),
                position_id=int(float(row["position_id"])), time=_parse_deal_time(row["time"]),
                type=int(float(row["type"])), entry=int(float(row["entry"])),
                symbol=row["symbol"], volume=float(row["volume"]), price=float(row["price"]),
                profit=float(row["profit"]), commission=float(row["commission"]),
                swap=float(row["swap"]), fee=float(row["fee"]),
                magic=int(float(row["magic"])), comment=row["comment"],
            )
            for row in parse_dataframe_csv(raw)
        ]
