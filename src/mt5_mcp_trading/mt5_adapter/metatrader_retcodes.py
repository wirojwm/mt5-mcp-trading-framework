"""
Parses metatrader-mcp-server trading-tool responses without ever trusting the tool's own
`error`/`success` field -- see docs/mcp_tool_classification.md, Known Issues item 7:
`send_order()` decides `success` from `mt5.last_error()` (terminal-level), never
`response.retcode` (the broker's actual decision). A genuinely rejected trade can report
`error: False`. Every caller must read `retcode` out of the raw response `data` field itself.

Confirmed by reading `metatrader_client/order/send_order.py`/`place_pending_order.py`/
`cancel_pending_order.py` directly: on a pre-flight rejection (invalid symbol/price/volume,
caught before `mt5.order_send()` is ever called), the tool returns `data: None` -- there is no
retcode to read at all in that case, not just an unfavorable one. `parse_trade_response()`
represents that as `retcode=None`, distinct from a real broker retcode.

LIVE-CONFIRMED (Phase 6 Step 4, see docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md): once
`mt5.order_send()` IS called, `data` does NOT come back as a JSON object/dict. Whatever
serializes `MetaTrader5.order_send()`'s raw C-extension "OrderSendResult" object falls back to
iterating it positionally, producing a JSON array in MT5's documented field order instead:
`retcode, deal, order, volume, price, bid, ask, comment, request_id, retcode_external,
request` (the last element itself a nested, also-positional `TradeRequest` array). Confirmed
against a real captured rejection (AutoTrading disabled in the terminal, retcode 10027):
`[10027, 0, 0, 0.0, 0.0, 0.0, 0.0, "AutoTrading disabled by client", 0, 0, [...]]`. Before this
was confirmed live, this module defensively required `data` to be a dict and raised
`MalformedTradeResponseError` otherwise -- which is exactly what happened, safely: no state
was written, nothing was misinterpreted as success. Kept dict support below as a second
accepted shape in case a future package version changes this, but the list form is what's
actually been observed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

TRADE_RETCODE_DONE = 10009  # the only retcode value that means "executed" -- MQL5 documented

# MT5's documented OrderSendResult field order -- see this module's docstring.
_ORDER_SEND_RESULT_FIELDS = (
    "retcode", "deal", "order", "volume", "price", "bid", "ask", "comment",
    "request_id", "retcode_external", "request",
)


class MalformedTradeResponseError(RuntimeError):
    """Raised when a trading tool's response doesn't match either documented OrderSendResult
    shape (list or dict) at all -- never silently treated as success or failure."""


@dataclass(frozen=True, slots=True)
class TradeResponse:
    # None when the request was rejected before MT5.order_send() was ever called (no `data`
    # at all) -- distinct from a real, unfavorable broker retcode.
    retcode: Optional[int]
    raw_data: Optional[dict]
    # The tool's own message, for logging/debugging only -- never used to determine success.
    tool_message: str

    @property
    def done(self) -> bool:
        return self.retcode == TRADE_RETCODE_DONE


def parse_trade_response(raw_json: str) -> TradeResponse:
    parsed = json.loads(raw_json)
    message = str(parsed.get("message", ""))
    data = parsed.get("data")

    if data is None:
        return TradeResponse(retcode=None, raw_data=None, tool_message=message)

    if isinstance(data, list):
        if len(data) < len(_ORDER_SEND_RESULT_FIELDS):
            raise MalformedTradeResponseError(
                f"expected at least {len(_ORDER_SEND_RESULT_FIELDS)} positional "
                f"OrderSendResult fields, got {len(data)}: {data!r}"
            )
        data = dict(zip(_ORDER_SEND_RESULT_FIELDS, data))
    elif not isinstance(data, dict):
        raise MalformedTradeResponseError(
            f"expected 'data' to be a list, dict, or null, got {type(data).__name__}: {data!r}"
        )

    if "retcode" not in data:
        raise MalformedTradeResponseError(f"'data' has no 'retcode' field: {data!r}")

    return TradeResponse(retcode=int(data["retcode"]), raw_data=data, tool_message=message)
