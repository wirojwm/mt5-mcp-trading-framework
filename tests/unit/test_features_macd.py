"""
Expected values cross-checked against the legacy formula (both EMAs over closes[-slow:],
adjust=False recursion) in a standalone scratch script -- not derived from this
implementation itself or from importing the legacy code.
"""

from __future__ import annotations

import pytest

from mt5_mcp_trading.features.macd import macd

UPWARD_CLOSES = [
    100.0, 100.59856699614471, 99.66109388420138, 99.34866718012418, 98.90669402549624,
    99.74787206090626, 100.43962077946354, 101.67006969872565, 100.88741678029919,
    100.94222132951236, 100.01671437810754, 99.56330931511654, 99.82669753537495,
    98.8930374595846, 98.39013158630122, 99.01484268075004, 99.37719638225808,
    98.92829793735982, 99.40146214704959, 100.42503828874415, 99.4412851879393,
    100.45583331752132, 101.20118180499189, 101.05180809628688, 100.44050684581633,
    101.83353952633328, 101.67502588911485, 100.90689049756521, 100.14868143964887,
    101.26741735551752, 101.77673243393475, 102.7945531171207, 103.61888258385524,
    103.95945281249199, 105.39224222244042, 105.3385781654613, 105.71867974364437,
    106.79219140427686, 107.33849078518747, 108.49275803596441,
]

DOWNWARD_CLOSES = [
    100.0, 100.1904180879171, 100.81329515310584, 100.1859589705062, 101.00486825383734,
    100.66516324307062, 100.75094095078916, 101.6059436388524, 101.33735430587885,
    102.24361515977388, 102.15950095061791, 102.98486239168136, 103.7580798583217,
    103.69678188546541, 102.62965157378532, 103.32014667091121, 103.76204925939368,
    103.1934662033797, 101.82419384723718, 101.38143647569343, 101.38973528906648,
    99.94909752508418, 100.83264082353979, 99.68646967591809, 99.9624464600889,
    100.6018087516953, 101.30732815649938, 101.53612359624455, 100.49580769844447,
    101.04399174863462, 100.58999133947846, 99.992707667163, 100.06171381034868,
    99.69235264607478, 100.53538020864147, 101.38637728372589, 101.87148050167758,
    101.17048056872312, 101.10149980454962, 101.31613187860764,
]


def test_macd_positive_on_upward_series() -> None:
    result = macd(UPWARD_CLOSES, fast=12, slow=26)
    assert result == pytest.approx(2.070015523156414, abs=1e-9)
    assert result > 0


def test_macd_negative_on_downward_series() -> None:
    result = macd(DOWNWARD_CLOSES, fast=12, slow=26)
    assert result == pytest.approx(-0.203279425650166, abs=1e-9)
    assert result < 0


def test_macd_exactly_zero_on_constant_series() -> None:
    # Constant closes -> both EMAs converge to the same constant -> exactly 0.0, not just
    # close to it.
    assert macd([100.0] * 40, fast=12, slow=26) == 0.0


def test_macd_returns_zero_when_fewer_than_max_fast_slow_closes() -> None:
    # Guard is len(closes) < max(fast, slow), i.e. 26 here -- 20 closes is insufficient.
    assert macd(UPWARD_CLOSES[:20], fast=12, slow=26) == 0.0


def test_macd_boundary_exactly_max_fast_slow_closes_is_sufficient() -> None:
    # len(closes) == max(fast, slow) should NOT trigger the insufficient-data guard.
    assert len(UPWARD_CLOSES[:26]) == max(12, 26)
    result = macd(UPWARD_CLOSES[:26], fast=12, slow=26)
    assert result == pytest.approx(0.2759492621161286, abs=1e-9)


def test_macd_fast_greater_equal_slow_is_exactly_zero() -> None:
    # Misconfiguration guard: fast >= slow makes both EMA calls use `slow`, forcing 0.0.
    assert macd(UPWARD_CLOSES, fast=30, slow=26) == 0.0


def test_macd_uses_only_the_last_slow_closes_not_full_history() -> None:
    # Prepending extra history must not change the result -- confirms the windowing
    # behavior (closes[-slow:]) is preserved, not accidentally using full history.
    padded = [9999.0, -9999.0, 12345.0] + list(UPWARD_CLOSES)
    assert macd(padded, fast=12, slow=26) == macd(UPWARD_CLOSES, fast=12, slow=26)
