"""
Loss-based kill-switch, Phase 9 Step 2 (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md). No legacy
precedent -- risk/__init__.py already documents that daily shutdown rules were never ported from
the legacy project, since there was nothing there to port; this is new, project-original design,
the hard blocker AGENTS.md's Live pilot entry has flagged since before Phase 9 was scoped.

Same independent/composable shape as portfolio_guards.py/symbol_guards.py (RiskDecision,
combine()-compatible) -- deliberately NOT yet wired into pipeline/grid_cycle.py,
pipeline/runner_cycle.py, or pipeline/loop_control.py. That wiring is Phase 9 Step 3, a separate,
later, explicitly-approved step (see the checkpoint doc's own step table). This module is pure
(no adapter imports, enforced by tests/test_architecture.py) and has no knowledge of *how*
realized P&L is actually computed -- that's an execution/adapter-layer concern (a real demo P&L
figure would come from a live account/deals read); this module only ever receives an
already-computed number, exactly like every other guard here receives already-computed
open/pending lots rather than fetching them itself.

Unit is raw account-currency $ (Phase 9 checkpoint doc, "Open design points" #1) -- the simplest
unit a human can directly cross-check against the MT5 terminal UI, consistent with ExposureCaps'
own raw-lots convention rather than R-multiples or % equity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from mt5_mcp_trading.domain.models import RiskDecision


@dataclass(frozen=True, slots=True)
class DailyLossLimitConfig:
    max_daily_loss: Optional[float] = None  # account-currency $, a non-negative magnitude
    reset_hour_utc: int = 0  # 0-23: hour of day (UTC) a new loss-tracking window begins


def check_daily_loss_limit(
    realized_pnl_since_reset: float,
    limit: DailyLossLimitConfig,
) -> RiskDecision:
    """TRIGGER: rejects (kill-switch tripped) once accumulated realized P&L since the last reset
    boundary is a loss whose magnitude is AT LEAST max_daily_loss --
    realized_pnl_since_reset <= -max_daily_loss. Deliberately at-or-beyond, not strictly-beyond:
    unlike check_exposure_cap()'s "exactly at cap is not a violation" convention (a soft sizing
    cap), this is a safety-critical stop-loss-shaped gate -- the same convention a real SL order
    uses (triggers on reaching the level, not just crossing past it) -- so reaching the limit
    exactly must trip it, not silently allow one more loss past it. `limit.max_daily_loss=None`
    (the default) always approves, matching every other Optional guard field in this codebase
    (ExposureCaps, GridStrategyConfig.max_entry_efficiency_ratio, etc.) -- off unless configured.

    Profit, or a loss strictly smaller in magnitude than the limit, never trips this.

    Caller's responsibility, since this function has no adapter access and cannot do it itself:
    compute `realized_pnl_since_reset` as the sum of realized P&L for every trade closed at or
    after `daily_reset_boundary()`'s return value, for whichever magic(s) are in scope. Not done
    here or anywhere yet -- that live computation is Phase 9 Step 3/4's job, not this guard's."""
    if limit.max_daily_loss is None:
        return RiskDecision(approved=True, reasons=("no daily loss limit configured",))

    if limit.max_daily_loss < 0:
        raise ValueError(f"max_daily_loss must be a non-negative magnitude, got {limit.max_daily_loss}")

    # Must be a genuine loss (< 0), not just non-positive -- guards max_daily_loss=0.0 (the most
    # conservative setting) from misfiring on an exact breakeven (0.0), where a naive
    # `pnl <= -limit` reads as true (0.0 <= -0.0) despite no loss having occurred at all.
    if realized_pnl_since_reset < 0 and -realized_pnl_since_reset >= limit.max_daily_loss:
        return RiskDecision(
            approved=False,
            reasons=(
                f"realized_pnl_since_reset={realized_pnl_since_reset:.2f} breaches "
                f"max_daily_loss={limit.max_daily_loss:.2f}",
            ),
            blocking_guard="risk.daily_loss_limit",
        )

    return RiskDecision(
        approved=True,
        reasons=(
            f"realized_pnl_since_reset={realized_pnl_since_reset:.2f} within "
            f"max_daily_loss={limit.max_daily_loss:.2f}",
        ),
    )


def daily_reset_boundary(now: datetime, reset_hour_utc: int = 0) -> datetime:
    """RESET: the canonical start of the current loss-tracking window -- the most recent instant
    at `reset_hour_utc:00:00 UTC` at or before `now`. One shared, tested definition so every
    future caller (Step 3's live wiring, any monitoring script) agrees on exactly when a new
    window begins, rather than each reimplementing "start of day" ad hoc and risking drift.

    `now` must be timezone-aware (this project's own convention -- MarketBar.time and every other
    timestamp in this codebase is UTC-aware); a naive datetime is rejected rather than silently
    assumed to already be UTC. A non-UTC aware datetime is converted, not rejected."""
    if now.tzinfo is None:
        raise ValueError("daily_reset_boundary() requires a timezone-aware datetime")
    if not 0 <= reset_hour_utc <= 23:
        raise ValueError(f"reset_hour_utc must be 0-23, got {reset_hour_utc}")

    now_utc = now.astimezone(timezone.utc)
    boundary = now_utc.replace(hour=reset_hour_utc, minute=0, second=0, microsecond=0)
    if boundary > now_utc:
        boundary -= timedelta(days=1)
    return boundary
