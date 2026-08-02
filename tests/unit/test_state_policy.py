"""
determine_posture(): pure decision logic, no I/O.
"""

from __future__ import annotations

import pytest

from mt5_mcp_trading.state.models import ReconciliationReport
from mt5_mcp_trading.state.policy import ExecutionPosture, determine_posture


def test_no_unknown_real_is_normal() -> None:
    report = ReconciliationReport(matched=(1,), local_only=(), unknown_real=())
    assert determine_posture(report, state_load_error=None) is ExecutionPosture.NORMAL


def test_empty_report_is_normal() -> None:
    report = ReconciliationReport(matched=(), local_only=(), unknown_real=())
    assert determine_posture(report, state_load_error=None) is ExecutionPosture.NORMAL


def test_local_only_alone_is_still_normal() -> None:
    # A locally-tracked order closed/cancelled outside this process isn't itself dangerous --
    # nothing untracked is loose on the account.
    report = ReconciliationReport(matched=(), local_only=(5,), unknown_real=())
    assert determine_posture(report, state_load_error=None) is ExecutionPosture.NORMAL


def test_unknown_real_forces_manage_only() -> None:
    report = ReconciliationReport(matched=(), local_only=(), unknown_real=(999,))
    assert determine_posture(report, state_load_error=None) is ExecutionPosture.MANAGE_ONLY


def test_state_load_error_forces_blocked_even_with_a_report() -> None:
    # state_load_error takes priority -- a report shouldn't even exist in this case in
    # practice, but the function must not be tricked by one being passed anyway.
    report = ReconciliationReport(matched=(1,), local_only=(), unknown_real=())
    posture = determine_posture(report, state_load_error=RuntimeError("corrupted"))
    assert posture is ExecutionPosture.BLOCKED


def test_state_load_error_alone_forces_blocked() -> None:
    posture = determine_posture(None, state_load_error=RuntimeError("corrupted"))
    assert posture is ExecutionPosture.BLOCKED


def test_neither_report_nor_error_raises() -> None:
    with pytest.raises(ValueError):
        determine_posture(None, state_load_error=None)
