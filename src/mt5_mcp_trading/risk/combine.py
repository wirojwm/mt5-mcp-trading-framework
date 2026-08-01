"""
Combines multiple independent RiskDecisions (e.g. one from a portfolio guard, one from a
symbol guard) into a single outcome for order_planning to act on.

Not a migration -- the legacy project never had a composable guard system to combine (its
caps/dedup checks were inline `if` statements in launcher_grid._grid_worker, evaluated ad hoc).
This is the answer to the question risk/__init__.py's docstring left open: reject if *any*
guard rejects (first rejection wins for blocking_guard, matching "no guard here may ever be
skippable by another guard passing" from AGENTS.md); approve only if every guard approves,
with every guard's reasons carried through either way for a full audit trail.
"""

from __future__ import annotations

from typing import Sequence

from mt5_mcp_trading.domain.models import RiskDecision


def combine(decisions: Sequence[RiskDecision]) -> RiskDecision:
    if not decisions:
        raise ValueError("combine() requires at least one RiskDecision")

    all_reasons: list[str] = []
    for decision in decisions:
        all_reasons.extend(decision.reasons)

    for decision in decisions:
        if not decision.approved:
            return RiskDecision(
                approved=False,
                reasons=tuple(all_reasons),
                blocking_guard=decision.blocking_guard,
            )

    return RiskDecision(approved=True, reasons=tuple(all_reasons))
