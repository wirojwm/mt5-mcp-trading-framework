#!/usr/bin/env python3
"""
Verifies the REAL wired `_compute_daily_loss_decision()` (scripts/run_demo_execution_pipeline_loop.py)
against real live data, after both 2026-08-07 fixes (Deal.time offset + all_records() trusted-set
sourcing) -- calls the actual function via importlib, not a hand-rolled mirror, so this proves the
real wiring, not just the logic in isolation.

READ-ONLY: only calls get_deals under the hood (already READ_ONLY-classified). No `executor`
reference, no order of any kind.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.risk.daily_loss_guard import DailyLossLimitConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

_logger = get_logger("mt5_mcp_trading.scripts.verify_kill_switch_fix")


def _load_loop_module():
    spec = importlib.util.spec_from_file_location(
        "pipeline_loop_under_test", PROJECT_ROOT / "scripts" / "run_demo_execution_pipeline_loop.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    module = _load_loop_module()
    config = DailyLossLimitConfig(max_daily_loss=0.01, reset_hour_utc=0)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor  # deliberately never used -- read-only diagnostic
        decision = await module._compute_daily_loss_decision(client, state_store, config)

    print(f"\n=== REAL _compute_daily_loss_decision() with max_daily_loss=0.01: "
          f"approved={decision.approved} ===")
    print(f"    reasons: {decision.reasons}")
    print(f"    blocking_guard: {decision.blocking_guard}\n")


if __name__ == "__main__":
    asyncio.run(main())
