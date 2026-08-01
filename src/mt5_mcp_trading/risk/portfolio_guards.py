"""
Portfolio-level exposure cap, ported from the legacy project's launcher_grid._grid_worker:

    cap = state.get("max_open_lots") or mm.max_open_lots_hint
    budget = state.get("budget_max_lots")
    if cap and vol_all >= cap: skip
    if budget and vol_all >= budget: skip

where vol_all = current open + pending lots for the symbol/magic.

One deliberate change, not a bug fix but an intentional strengthening: the legacy check only
looks at *current* exposure (vol_all) before deciding whether to seed a new order this cycle --
it never accounts for the volume about to be added, so it's possible to jump from just-under
the cap to over it in a single order. That was tolerable there because money_management.py's
own docstring describes these caps as soft "hints" that "the orchestrator/caps really enforce"
elsewhere. This risk/ layer *is* that real enforcement point (per this project's architecture:
"Never bypass portfolio guards ... or broker constraints"), so check_exposure_cap() checks
*projected* exposure (current + proposed_volume) against the caps instead. Documented here
rather than silently changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from mt5_mcp_trading.domain.models import RiskDecision


@dataclass(frozen=True, slots=True)
class ExposureCaps:
    max_open_lots: Optional[float] = None
    budget_max_lots: Optional[float] = None


def check_exposure_cap(
    open_lots: float,
    pending_lots: float,
    proposed_volume: float,
    caps: ExposureCaps,
) -> RiskDecision:
    projected_total = open_lots + pending_lots + proposed_volume
    reasons: list[str] = [
        f"open={open_lots}, pending={pending_lots}, proposed={proposed_volume}, "
        f"projected_total={projected_total}"
    ]

    if caps.max_open_lots is not None and projected_total > caps.max_open_lots:
        reasons.append(f"projected_total={projected_total} exceeds max_open_lots={caps.max_open_lots}")
        return RiskDecision(approved=False, reasons=tuple(reasons), blocking_guard="portfolio.max_open_lots")

    if caps.budget_max_lots is not None and projected_total > caps.budget_max_lots:
        reasons.append(f"projected_total={projected_total} exceeds budget_max_lots={caps.budget_max_lots}")
        return RiskDecision(approved=False, reasons=tuple(reasons), blocking_guard="portfolio.budget_max_lots")

    reasons.append("within configured caps")
    return RiskDecision(approved=True, reasons=tuple(reasons))
