from __future__ import annotations

import asyncio
import logging

from mt5_mcp_trading.domain.models import OrderPlan
from mt5_mcp_trading.execution.dry_run import DryRunExecutor


def _plan() -> OrderPlan:
    return OrderPlan(symbol="BTCUSD", order_type="LIMIT", side="BUY", volume=0.01,
                      price=63000.0, sl=0.0, tp=0.0, deviation=150, magic=71101, comment="grid")


def test_submit_never_touches_anything_real_and_records_the_plan() -> None:
    executor = DryRunExecutor()
    result = asyncio.run(executor.submit(_plan()))
    assert result.success is True
    assert executor.submitted == [_plan()]


def test_cancel_and_close_are_recorded_not_sent_anywhere() -> None:
    executor = DryRunExecutor()
    asyncio.run(executor.cancel(42))
    asyncio.run(executor.close_position(43, volume=0.02))
    assert executor.cancelled == [42]
    assert executor.closed == [(43, 0.02)]


def test_submit_logs_at_info_level(caplog) -> None:
    executor = DryRunExecutor()
    with caplog.at_level(logging.INFO, logger="mt5_mcp_trading.execution.dry_run"):
        asyncio.run(executor.submit(_plan()))
    assert any("would submit" in r.message for r in caplog.records)


def test_uses_the_given_logger_instance_when_provided() -> None:
    custom_logger = logging.getLogger("test.custom.dry_run")
    executor = DryRunExecutor(logger=custom_logger)
    assert executor._logger is custom_logger
