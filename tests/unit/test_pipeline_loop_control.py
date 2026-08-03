from __future__ import annotations

from mt5_mcp_trading.pipeline.loop_control import LoopLimits, should_stop


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
