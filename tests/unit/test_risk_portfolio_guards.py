from __future__ import annotations

from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps, check_exposure_cap


def test_approved_when_projected_total_within_caps() -> None:
    caps = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.08)
    result = check_exposure_cap(open_lots=0.02, pending_lots=0.01, proposed_volume=0.01, caps=caps)
    assert result.approved is True
    assert result.blocking_guard is None


def test_rejected_when_projected_total_exceeds_max_open_lots() -> None:
    caps = ExposureCaps(max_open_lots=0.06)
    # 0.02 + 0.01 + 0.04 = 0.07 > 0.06
    result = check_exposure_cap(open_lots=0.02, pending_lots=0.01, proposed_volume=0.04, caps=caps)
    assert result.approved is False
    assert result.blocking_guard == "portfolio.max_open_lots"


def test_rejected_when_projected_total_exceeds_budget_max_lots_even_if_under_max_open_lots() -> None:
    caps = ExposureCaps(max_open_lots=1.0, budget_max_lots=0.05)
    result = check_exposure_cap(open_lots=0.02, pending_lots=0.01, proposed_volume=0.04, caps=caps)
    assert result.approved is False
    assert result.blocking_guard == "portfolio.budget_max_lots"


def test_exactly_at_cap_is_not_a_violation_only_strictly_over_is() -> None:
    caps = ExposureCaps(max_open_lots=0.06)
    # 0.02 + 0.01 + 0.03 = 0.06, exactly at cap -- not "exceeds"
    result = check_exposure_cap(open_lots=0.02, pending_lots=0.01, proposed_volume=0.03, caps=caps)
    assert result.approved is True


def test_no_caps_configured_always_approves() -> None:
    result = check_exposure_cap(open_lots=100.0, pending_lots=100.0, proposed_volume=100.0, caps=ExposureCaps())
    assert result.approved is True


def test_checks_projected_not_just_current_exposure() -> None:
    # Deliberate strengthening over the legacy behavior (see module docstring): a proposed
    # order that would push exposure over the cap must be rejected even though *current*
    # exposure alone is still under it.
    caps = ExposureCaps(max_open_lots=0.05)
    result = check_exposure_cap(open_lots=0.04, pending_lots=0.0, proposed_volume=0.03, caps=caps)
    assert result.approved is False  # current (0.04) is under 0.05, but projected (0.07) is not
