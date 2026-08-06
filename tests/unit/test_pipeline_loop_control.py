from __future__ import annotations

from mt5_mcp_trading.domain.models import RiskDecision
from mt5_mcp_trading.pipeline.loop_control import LoopLimits, should_stop
from mt5_mcp_trading.risk.daily_loss_guard import DailyLossLimitConfig, check_daily_loss_limit


def _limits(**overrides) -> LoopLimits:
    defaults = dict(
        max_cycles=12, max_runtime_seconds=5400.0, cycle_interval_seconds=300.0,
        poll_interval_seconds=5.0,
    )
    defaults.update(overrides)
    return LoopLimits(**defaults)


def test_keeps_going_when_nothing_applies() -> None:
    assert should_stop(cycle_num=3, elapsed_seconds=900.0, stop_file_exists=False,
                        limits=_limits()) is None


def test_stop_file_present_stops_regardless_of_other_bounds() -> None:
    reason = should_stop(cycle_num=0, elapsed_seconds=0.0, stop_file_exists=True, limits=_limits())
    assert reason == "stop file present"


def test_max_cycles_reached_stops() -> None:
    reason = should_stop(cycle_num=12, elapsed_seconds=100.0, stop_file_exists=False,
                          limits=_limits(max_cycles=12))
    assert reason == "max cycles (12) reached"


def test_max_cycles_exceeded_still_stops() -> None:
    # >= , not == -- must not require an exact match to trigger.
    reason = should_stop(cycle_num=15, elapsed_seconds=100.0, stop_file_exists=False,
                          limits=_limits(max_cycles=12))
    assert reason == "max cycles (12) reached"


def test_below_max_cycles_does_not_stop_on_cycle_count_alone() -> None:
    reason = should_stop(cycle_num=11, elapsed_seconds=100.0, stop_file_exists=False,
                          limits=_limits(max_cycles=12))
    assert reason is None


def test_max_runtime_reached_stops() -> None:
    reason = should_stop(cycle_num=1, elapsed_seconds=5400.0, stop_file_exists=False,
                          limits=_limits(max_runtime_seconds=5400.0))
    assert reason == "max runtime (5400s) reached"


def test_max_runtime_exceeded_still_stops() -> None:
    reason = should_stop(cycle_num=1, elapsed_seconds=9000.0, stop_file_exists=False,
                          limits=_limits(max_runtime_seconds=5400.0))
    assert reason == "max runtime (5400s) reached"


def test_below_max_runtime_does_not_stop_on_elapsed_time_alone() -> None:
    reason = should_stop(cycle_num=1, elapsed_seconds=5399.0, stop_file_exists=False,
                          limits=_limits(max_runtime_seconds=5400.0))
    assert reason is None


def test_stop_file_takes_precedence_over_max_cycles() -> None:
    reason = should_stop(cycle_num=999, elapsed_seconds=0.0, stop_file_exists=True,
                          limits=_limits(max_cycles=12))
    assert reason == "stop file present"


def test_max_cycles_takes_precedence_over_max_runtime_when_both_apply() -> None:
    reason = should_stop(cycle_num=12, elapsed_seconds=9000.0, stop_file_exists=False,
                          limits=_limits(max_cycles=12, max_runtime_seconds=5400.0))
    assert reason == "max cycles (12) reached"


# --- Phase 9 Step 3: daily_loss_decision wiring -------------------------------------------------

def test_daily_loss_decision_omitted_matches_pre_step_3_behavior() -> None:
    # Default (no daily_loss_decision passed at all) must be byte-for-bit identical to every
    # pre-Step-3 call site -- proves this is a purely additive, backward-compatible change.
    assert should_stop(cycle_num=3, elapsed_seconds=900.0, stop_file_exists=False,
                        limits=_limits()) is None


def test_daily_loss_decision_none_does_not_stop() -> None:
    reason = should_stop(cycle_num=3, elapsed_seconds=900.0, stop_file_exists=False,
                          limits=_limits(), daily_loss_decision=None)
    assert reason is None


def test_daily_loss_decision_approved_does_not_stop() -> None:
    approved = RiskDecision(approved=True, reasons=("within configured limit",))
    reason = should_stop(cycle_num=3, elapsed_seconds=900.0, stop_file_exists=False,
                          limits=_limits(), daily_loss_decision=approved)
    assert reason is None


def test_daily_loss_breach_stops_the_loop() -> None:
    breached = RiskDecision(approved=False, reasons=("realized_pnl_since_reset=-600.00 breaches "
                                                       "max_daily_loss=500.00",),
                             blocking_guard="risk.daily_loss_limit")
    reason = should_stop(cycle_num=3, elapsed_seconds=900.0, stop_file_exists=False,
                          limits=_limits(), daily_loss_decision=breached)
    assert reason == ("daily loss limit breached (realized_pnl_since_reset=-600.00 breaches "
                       "max_daily_loss=500.00)")


def test_daily_loss_breach_via_real_check_daily_loss_limit() -> None:
    # Not a hand-built RiskDecision this time -- the real Step 2 function, proving actual
    # end-to-end interop rather than just RiskDecision's shape being compatible in principle.
    decision = check_daily_loss_limit(
        realized_pnl_since_reset=-750.0, limit=DailyLossLimitConfig(max_daily_loss=500.0),
    )
    reason = should_stop(cycle_num=3, elapsed_seconds=900.0, stop_file_exists=False,
                          limits=_limits(), daily_loss_decision=decision)
    assert reason is not None
    assert "daily loss limit breached" in reason


def test_stop_file_takes_precedence_over_daily_loss_breach() -> None:
    breached = RiskDecision(approved=False, reasons=("breach",), blocking_guard="risk.daily_loss_limit")
    reason = should_stop(cycle_num=3, elapsed_seconds=900.0, stop_file_exists=True,
                          limits=_limits(), daily_loss_decision=breached)
    assert reason == "stop file present"


def test_daily_loss_breach_takes_precedence_over_max_cycles() -> None:
    breached = RiskDecision(approved=False, reasons=("breach",), blocking_guard="risk.daily_loss_limit")
    reason = should_stop(cycle_num=999, elapsed_seconds=100.0, stop_file_exists=False,
                          limits=_limits(max_cycles=12), daily_loss_decision=breached)
    assert reason == "daily loss limit breached (breach)"


def test_daily_loss_breach_takes_precedence_over_max_runtime() -> None:
    breached = RiskDecision(approved=False, reasons=("breach",), blocking_guard="risk.daily_loss_limit")
    reason = should_stop(cycle_num=1, elapsed_seconds=99999.0, stop_file_exists=False,
                          limits=_limits(max_runtime_seconds=5400.0), daily_loss_decision=breached)
    assert reason == "daily loss limit breached (breach)"
