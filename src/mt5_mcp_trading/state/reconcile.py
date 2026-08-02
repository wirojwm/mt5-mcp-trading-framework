"""
Pure reconciliation: cross-checks local OPEN order records against a live MT5 snapshot, by
ticket only. Never matches by symbol, side, price, or timing -- MT5-side magic is always 0 for
orders this project places (see docs/mcp_tool_classification.md, Known Issues item 7), which is
exactly the kind of field that could tempt a "helpful" guess. Guessing ownership is exactly what
this project has decided never to do; see policy.py for what an unmatched real ticket means for
whether new orders may be submitted.
"""

from __future__ import annotations

from typing import Sequence

from mt5_mcp_trading.domain.models import OrderState, PositionState
from mt5_mcp_trading.state.models import LocalOrderRecord, ReconciliationReport


def reconcile(
    local_open: Sequence[LocalOrderRecord],
    real_positions: Sequence[PositionState],
    real_orders: Sequence[OrderState],
) -> ReconciliationReport:
    local_tickets = {r.ticket for r in local_open}
    real_tickets = {p.ticket for p in real_positions} | {o.ticket for o in real_orders}

    return ReconciliationReport(
        matched=tuple(sorted(local_tickets & real_tickets)),
        local_only=tuple(sorted(local_tickets - real_tickets)),
        unknown_real=tuple(sorted(real_tickets - local_tickets)),
    )
