"""
Local order-state records and reconciliation results.

LocalOrderRecord captures everything this project intended when it submitted an order --
including magic/comment/deviation/filling_mode/expiry, which metatrader-mcp-server's trading
tools silently drop or override before they ever reach MT5 (see
docs/mcp_tool_classification.md, Known Issues item 7). MT5 itself will report magic=0,
comment="MCP" on the resulting real position/order; this record is the only place the intended
values still exist. Never treat a LocalOrderRecord as more trustworthy than the real MT5 state
it describes -- it's what we asked for, not proof of what happened. reconcile() (reconcile.py)
is what cross-checks it against reality, by ticket only, never by symbol/side/timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

OrderRecordStatus = Literal["OPEN", "CANCELLED", "CLOSED"]


@dataclass(frozen=True, slots=True)
class LocalOrderRecord:
    ticket: int
    # Explicit, caller-supplied identifier (e.g. "grid", "runner", "smoke_test") -- never
    # derived by guessing from symbol/comment text. See state/strategy_registry.py.
    strategy: str
    magic: int
    comment: str
    symbol: str
    side: str
    order_type: str
    # What was requested -- not what MT5 necessarily honored (see this module's docstring).
    requested_volume: float
    requested_price: Optional[float]
    requested_sl: float
    requested_tp: float
    requested_deviation: int
    requested_filling_mode: Optional[str]
    requested_expiry: Optional[datetime]
    # The raw execution response actually received.
    retcode: int
    executed_price: Optional[float]
    executed_volume: Optional[float]
    broker_comment: str
    submitted_at: datetime
    closed_at: Optional[datetime]
    status: OrderRecordStatus
    closed_reason: Optional[str]


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Result of cross-checking local OPEN records against a live MT5 snapshot, by ticket
    only. See state/reconcile.py."""

    matched: tuple[int, ...]
    # Locally OPEN, absent from the current real snapshot -- closed/cancelled outside this
    # process (manually, by SL/TP, by another tool). Not itself an error condition.
    local_only: tuple[int, ...]
    # Present in the real MT5 snapshot, no local record at all. Never attributed to a
    # magic/strategy by guessing -- reported here, not resolved. See state/policy.py for what
    # this means for whether new orders may be submitted.
    unknown_real: tuple[int, ...]
