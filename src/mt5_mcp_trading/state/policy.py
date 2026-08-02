"""
Execution posture: what determine_posture() computes from a ReconciliationReport (or a state
load failure) to decide whether McpOrderExecutor may submit new orders at all.

Requirement this encodes: if local state is missing, inconsistent, or cannot be matched
safely, execution must degrade to manage-only or fully blocked -- never a silent guess at
ownership, and never "proceed as if everything is fine."
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from mt5_mcp_trading.state.models import ReconciliationReport


class ExecutionPosture(Enum):
    # Nothing unattributed found; local state loaded fine. submit()/cancel()/close_position()
    # all allowed (subject to the per-call demo-account gates).
    NORMAL = "NORMAL"
    # Real positions/orders exist with no local record (state.models.ReconciliationReport.
    # unknown_real is non-empty) -- "cannot be matched safely". submit() must refuse new
    # orders; cancel()/close_position() remain allowed only for tickets this process can
    # actually attribute (matched or local_only), never for an unknown_real ticket.
    MANAGE_ONLY = "MANAGE_ONLY"
    # The local state file exists but couldn't be parsed/trusted at all -- "inconsistent".
    # Nothing acts on any ticket, submit or otherwise, until a human resolves the file.
    BLOCKED = "BLOCKED"


def determine_posture(
    report: Optional[ReconciliationReport], state_load_error: Optional[BaseException]
) -> ExecutionPosture:
    if state_load_error is not None:
        return ExecutionPosture.BLOCKED
    if report is None:
        raise ValueError("determine_posture requires either a report or a state_load_error")
    if report.unknown_real:
        return ExecutionPosture.MANAGE_ONLY
    return ExecutionPosture.NORMAL
