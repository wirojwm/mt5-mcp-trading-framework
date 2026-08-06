from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_mcp_trading.risk.combine import combine
from mt5_mcp_trading.risk.daily_loss_guard import (
    DailyLossLimitConfig,
    check_daily_loss_limit,
    daily_reset_boundary,
)
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps, check_exposure_cap


# --- check_daily_loss_limit ------------------------------------------------------------------

def test_no_limit_configured_always_approves() -> None:
    result = check_daily_loss_limit(realized_pnl_since_reset=-100_000.0, limit=DailyLossLimitConfig())
    assert result.approved is True
    assert result.blocking_guard is None


def test_approved_when_in_profit() -> None:
    limit = DailyLossLimitConfig(max_daily_loss=500.0)
    result = check_daily_loss_limit(realized_pnl_since_reset=250.0, limit=limit)
    assert result.approved is True


def test_approved_when_loss_strictly_below_limit() -> None:
    limit = DailyLossLimitConfig(max_daily_loss=500.0)
    result = check_daily_loss_limit(realized_pnl_since_reset=-499.99, limit=limit)
    assert result.approved is True


def test_rejected_exactly_at_limit_not_only_strictly_beyond() -> None:
    # Deliberately different from check_exposure_cap()'s "exactly at cap is not a violation"
    # convention -- this is a safety-critical stop-loss-shaped gate, so reaching the limit
    # exactly must trip it. See module docstring.
    limit = DailyLossLimitConfig(max_daily_loss=500.0)
    result = check_daily_loss_limit(realized_pnl_since_reset=-500.0, limit=limit)
    assert result.approved is False
    assert result.blocking_guard == "risk.daily_loss_limit"


def test_rejected_when_loss_exceeds_limit() -> None:
    limit = DailyLossLimitConfig(max_daily_loss=500.0)
    result = check_daily_loss_limit(realized_pnl_since_reset=-731.50, limit=limit)
    assert result.approved is False
    assert result.blocking_guard == "risk.daily_loss_limit"
    assert "731.5" in result.reasons[0]


def test_zero_max_daily_loss_trips_on_any_loss() -> None:
    limit = DailyLossLimitConfig(max_daily_loss=0.0)
    result = check_daily_loss_limit(realized_pnl_since_reset=-0.01, limit=limit)
    assert result.approved is False


def test_zero_max_daily_loss_still_approves_breakeven_or_profit() -> None:
    limit = DailyLossLimitConfig(max_daily_loss=0.0)
    assert check_daily_loss_limit(realized_pnl_since_reset=0.0, limit=limit).approved is True
    assert check_daily_loss_limit(realized_pnl_since_reset=10.0, limit=limit).approved is True


def test_negative_max_daily_loss_is_rejected_as_invalid_config() -> None:
    limit = DailyLossLimitConfig(max_daily_loss=-100.0)
    with pytest.raises(ValueError):
        check_daily_loss_limit(realized_pnl_since_reset=0.0, limit=limit)


def test_combine_compatible_with_other_guards() -> None:
    # Proves the "same shape as every other guard" design claim directly, without any pipeline
    # wiring: combine() (used today by grid_cycle.py/runner_cycle.py for the existing guards)
    # accepts this guard's RiskDecision exactly like any other, and a daily-loss rejection wins
    # exactly like any other guard's rejection would.
    loss_decision = check_daily_loss_limit(
        realized_pnl_since_reset=-1000.0, limit=DailyLossLimitConfig(max_daily_loss=500.0),
    )
    exposure_decision = check_exposure_cap(
        open_lots=0.0, pending_lots=0.0, proposed_volume=0.01, caps=ExposureCaps(max_open_lots=0.06),
    )
    assert exposure_decision.approved is True  # would pass on its own

    result = combine([exposure_decision, loss_decision])
    assert result.approved is False
    assert result.blocking_guard == "risk.daily_loss_limit"


# --- daily_reset_boundary ---------------------------------------------------------------------

def test_boundary_returns_todays_boundary_when_now_is_after_it() -> None:
    now = datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc)
    boundary = daily_reset_boundary(now, reset_hour_utc=0)
    assert boundary == datetime(2026, 8, 6, 0, 0, 0, tzinfo=timezone.utc)


def test_boundary_returns_yesterdays_boundary_when_now_is_before_it() -> None:
    now = datetime(2026, 8, 6, 3, 0, tzinfo=timezone.utc)
    boundary = daily_reset_boundary(now, reset_hour_utc=5)
    assert boundary == datetime(2026, 8, 5, 5, 0, 0, tzinfo=timezone.utc)


def test_boundary_exactly_at_reset_hour_returns_same_instant() -> None:
    now = datetime(2026, 8, 6, 5, 0, 0, tzinfo=timezone.utc)
    boundary = daily_reset_boundary(now, reset_hour_utc=5)
    assert boundary == now


def test_boundary_one_second_before_reset_hour_returns_previous_day() -> None:
    now = datetime(2026, 8, 6, 4, 59, 59, tzinfo=timezone.utc)
    boundary = daily_reset_boundary(now, reset_hour_utc=5)
    assert boundary == datetime(2026, 8, 5, 5, 0, 0, tzinfo=timezone.utc)


def test_boundary_converts_non_utc_timezone() -> None:
    utc_minus_5 = timezone(timedelta(hours=-5))
    now = datetime(2026, 8, 6, 20, 0, tzinfo=utc_minus_5)  # == 2026-08-07T01:00Z
    boundary = daily_reset_boundary(now, reset_hour_utc=0)
    assert boundary == datetime(2026, 8, 7, 0, 0, 0, tzinfo=timezone.utc)


def test_boundary_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        daily_reset_boundary(datetime(2026, 8, 6, 12, 0))


@pytest.mark.parametrize("bad_hour", [-1, 24, 100])
def test_boundary_rejects_invalid_reset_hour(bad_hour: int) -> None:
    with pytest.raises(ValueError):
        daily_reset_boundary(datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc), reset_hour_utc=bad_hour)
