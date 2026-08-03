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

`origin` distinguishes HOW a record came to exist:
- "system_owned": created by StateStore.record_submission() after a real McpOrderExecutor
  submission. `retcode`/`requested_*`/`executed_*` reflect an actual request/response cycle.
- "manual_adoption": created by StateStore.record_manual_adoption() for a position opened
  directly in the MT5 terminal, outside McpOrderExecutor entirely -- NEVER fabricated to look
  like a system submission. `retcode` is `None` (no submission ever happened), and
  `requested_*`/`executed_*` fields hold values independently OBSERVED via a live read, not
  values this project asked MT5 for. This exists for one narrow, explicitly-scoped case (Phase
  6 Step 5's demo smoke test, see docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md) where a
  human manually opened a position and explicitly approved treating it as locally attributed,
  after an exact live-verified match -- not a general "claim any position" mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

# OPEN_UNPROTECTED (Phase 6 Step 6): a MARKET order's position confirmed open via retcode,
# but its mandatory SL/TP follow-up (modify_position) has not been confirmed attached --
# either not yet attempted, rejected, or reported success but disagreeing with a fresh live
# read. Deliberately its own status, not a separate boolean, so a stale/partial write can
# never leave a naked position looking identical to a normally-protected OPEN one.
# StateStore.all_open() treats it as open (reconciliation must still recognize the ticket as
# locally known, so an unprotected position never becomes "unknown_real" and forces the whole
# executor into MANAGE_ONLY -- see mcp_order_executor.py's module docstring for why only that
# one ticket is meant to require special handling, not every other in-flight order). Never
# transitions automatically back to OPEN except via StateStore.mark_sl_tp_attached(), which is
# only ever called after BOTH a confirmed-done modify_position retcode AND a fresh live read
# agree SL/TP now matches what was requested.
OrderRecordStatus = Literal["OPEN", "OPEN_UNPROTECTED", "CANCELLED", "CLOSED"]
OrderRecordOrigin = Literal["system_owned", "manual_adoption"]


@dataclass(frozen=True, slots=True)
class LocalOrderRecord:
    ticket: int
    # Explicit, caller-supplied identifier (e.g. "grid", "runner", "smoke_test",
    # "manual_adoption") -- never derived by guessing from symbol/comment text. See
    # state/strategy_registry.py.
    strategy: str
    magic: int
    comment: str
    symbol: str
    side: str
    order_type: str
    # What was requested (system_owned) or independently observed (manual_adoption) -- see
    # this module's docstring. Never what MT5 necessarily honored.
    requested_volume: float
    requested_price: Optional[float]
    requested_sl: float
    requested_tp: float
    requested_deviation: int
    requested_filling_mode: Optional[str]
    requested_expiry: Optional[datetime]
    # The raw execution response actually received -- None for manual_adoption, since no
    # submission ever happened to receive a response from.
    retcode: Optional[int]
    executed_price: Optional[float]
    executed_volume: Optional[float]
    broker_comment: str
    submitted_at: datetime
    closed_at: Optional[datetime]
    status: OrderRecordStatus
    closed_reason: Optional[str]
    origin: OrderRecordOrigin


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
