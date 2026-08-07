#!/usr/bin/env python3
"""
Follow-up to run_demo_execution_check_step5_smoke_test_state.py: get_deals(from_date='2026-08-07')
found zero deals for 3 confirmed-closed runner tickets (171809336, 171809814, 171809876) even
queried fresh, well after the Step 5 smoke test run ended. This probe tries several date-range
variants against the SAME tickets to narrow down whether it's a same-day freshness/sync gap
specifically, or something else -- read-only throughout, no `executor` reference, no order.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

TARGET_TICKETS = {171809336, 171809814, 171809876}

VARIANTS = [
    ("from=2026-08-07 (today, no to_date)", dict(from_date="2026-08-07")),
    ("from=2026-08-07, to=2026-08-08 (explicit next-day to_date)",
     dict(from_date="2026-08-07", to_date="2026-08-08")),
    ("from=2026-08-06 (yesterday, no to_date)", dict(from_date="2026-08-06")),
    ("from=2026-08-01 (a week back, no to_date)", dict(from_date="2026-08-01")),
]

_logger = get_logger("mt5_mcp_trading.scripts.probe_get_deals_gap")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store  # deliberately never used -- read-only diagnostic
        reader = McpDealHistoryReader(client)

        print()
        for label, kwargs in VARIANTS:
            deals = await reader.get_deals(**kwargs)
            matched = [d for d in deals if d.position_id in TARGET_TICKETS]
            print(f"=== {label}: {len(deals)} total deal(s), "
                  f"{len(matched)} matching target tickets {sorted(TARGET_TICKETS)} ===")
            for d in sorted(matched, key=lambda d: d.time):
                print(f"  position_id={d.position_id} time={d.time.isoformat()} entry={d.entry} "
                      f"type={d.type} profit={d.profit}")
            if deals and not matched:
                sample_times = sorted(d.time.isoformat() for d in deals)[:3]
                print(f"  ({len(deals)} deals found but none match target tickets; "
                      f"earliest sample times: {sample_times})")
        print()


if __name__ == "__main__":
    asyncio.run(main())
