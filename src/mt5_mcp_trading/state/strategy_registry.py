"""
Explicit magic->strategy-name mapping. Keeps OrderExecutor.submit(order_plan)'s Protocol
signature unchanged (shared with DryRunExecutor/MockOrderExecutor) while still giving every
local order record (state/models.py) a real, non-guessed strategy identifier -- magic numbers
already carry this information by this project's own convention (see
tests/integration/*_dry_run_pipeline.py, pipeline/grid_cycle.py, pipeline/runner_cycle.py).

An unmapped magic returns an explicit, greppable "unknown_magic_<n>" rather than a generic
"unknown" -- deliberately loud, so a caller (or a human reading the state file later) can
immediately tell which magic wasn't recognized instead of having every unrecognized strategy
collapse into one indistinguishable bucket.
"""

from __future__ import annotations

_MAGIC_TO_STRATEGY: dict[int, str] = {
    71101: "grid",
    72101: "runner",
}


def strategy_name_for_magic(magic: int) -> str:
    return _MAGIC_TO_STRATEGY.get(magic, f"unknown_magic_{magic}")
