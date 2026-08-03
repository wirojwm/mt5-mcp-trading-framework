"""
Stop-decision logic for the bounded autonomous loop (scripts/run_demo_execution_pipeline_loop.py)
-- the one piece of genuinely new decision logic that loop introduces, kept here as a pure
function rather than buried in the script, so it can be tested directly. No daily-shutdown/
kill-switch/circuit-breaker concept existed anywhere in this codebase before this (confirmed:
risk/__init__.py explicitly says these "do not exist in the legacy project... would be new
functionality, not a migration") -- this is that functionality, deliberately isolated and
tested rather than assumed correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class LoopLimits:
    max_cycles: int
    max_runtime_seconds: float
    cycle_interval_seconds: float
    poll_interval_seconds: float  # how often to re-check the stop file while waiting


def should_stop(
    cycle_num: int, elapsed_seconds: float, stop_file_exists: bool, limits: LoopLimits,
) -> Optional[str]:
    """Returns a human-readable stop reason, or None to keep going. Checked before every cycle
    and during every inter-cycle wait. Precedence: stop-file first (an explicit human request
    always wins regardless of where the loop is in its bounds), then max_cycles, then
    max_runtime_seconds."""
    if stop_file_exists:
        return "stop file present"
    if cycle_num >= limits.max_cycles:
        return f"max cycles ({limits.max_cycles}) reached"
    if elapsed_seconds >= limits.max_runtime_seconds:
        return f"max runtime ({limits.max_runtime_seconds:.0f}s) reached"
    return None
