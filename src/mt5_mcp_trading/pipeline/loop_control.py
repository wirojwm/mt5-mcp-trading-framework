"""
Stop-decision logic for the bounded autonomous loop (scripts/run_demo_execution_pipeline_loop.py)
-- the one piece of genuinely new decision logic that loop introduces, kept here as a pure
function rather than buried in the script, so it can be tested directly. No daily-shutdown/
kill-switch/circuit-breaker concept existed anywhere in this codebase before Phase 9 (confirmed:
risk/__init__.py explicitly says these "do not exist in the legacy project... would be new
functionality, not a migration") -- this is that functionality, deliberately isolated and
tested rather than assumed correct.

Phase 9 Step 3 (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md) wires Step 2's loss-based kill-switch
(risk/daily_loss_guard.py) into should_stop() below -- this module still has no adapter access
and cannot compute realized P&L itself, exactly like it already can't check the stop-file itself
(stop_file_exists is passed in, not a path); the caller must resolve a RiskDecision first (e.g.
via risk.daily_loss_guard.check_daily_loss_limit()) and pass it in. Not yet wired into
scripts/run_demo_execution_pipeline_loop.py's own should_stop() call sites -- that script has no
real source for realized P&L yet (Phase 9 Step 4's job); wiring it here first with a real caller
still deferred keeps this decision logic honestly separate from a not-yet-real number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from mt5_mcp_trading.domain.models import RiskDecision


@dataclass(frozen=True, slots=True)
class LoopLimits:
    max_cycles: int
    max_runtime_seconds: float
    cycle_interval_seconds: float
    poll_interval_seconds: float  # how often to re-check the stop file while waiting


def should_stop(
    cycle_num: int, elapsed_seconds: float, stop_file_exists: bool, limits: LoopLimits,
    daily_loss_decision: Optional[RiskDecision] = None,
) -> Optional[str]:
    """Returns a human-readable stop reason, or None to keep going. Checked before every cycle
    and during every inter-cycle wait. Precedence: stop-file first (an explicit human request
    always wins regardless of where the loop is in its bounds), then a daily-loss-limit breach
    (a real-money safety stop, so it outranks the administrative max_cycles/max_runtime ceilings
    -- though never the explicit human stop-file request above it), then max_cycles, then
    max_runtime_seconds.

    `daily_loss_decision` defaults to None (off) -- every existing caller/test is completely
    unaffected unless it explicitly opts in, matching every other Optional guard field in this
    codebase (ExposureCaps, GridStrategyConfig.max_entry_efficiency_ratio, etc.). When supplied,
    it must already be a resolved RiskDecision (typically from
    risk.daily_loss_guard.check_daily_loss_limit()) -- an approved decision never stops the loop;
    only `daily_loss_decision.approved is False` does."""
    if stop_file_exists:
        return "stop file present"
    if daily_loss_decision is not None and not daily_loss_decision.approved:
        detail = "; ".join(daily_loss_decision.reasons) or "no reason given"
        return f"daily loss limit breached ({detail})"
    if cycle_num >= limits.max_cycles:
        return f"max cycles ({limits.max_cycles}) reached"
    if elapsed_seconds >= limits.max_runtime_seconds:
        return f"max runtime ({limits.max_runtime_seconds:.0f}s) reached"
    return None
