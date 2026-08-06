#!/usr/bin/env python3
"""
One-off diagnostic (not part of Phase 9's scoped deliverables): confirms WHY
run_demo_execution_live_performance_monitor.py's 2026-08-06 run skipped tickets 171648990/
171649461 (see docs/PHASE9_FORWARD_TEST_CHECKPOINT.md's live-run entry) -- both local records
are LIMIT orders with executed_price=0.0, reconciled CLOSED via a generic "confirmed absent"
reason that can't distinguish "filled then closed" from "cancelled/expired unfilled". The
monitor's get_deals read already found zero deals for either ticket in the correct date window,
strongly suggesting they never filled at all.

READ-ONLY: calls get_orders directly (already READ_ONLY-classified in metatrader_tools.py) --
MT5's pending-order HISTORY tool (not get_deals), which is where a cancelled/expired-without-
filling order shows up (a deal only ever exists for a real execution). No `executor` reference.
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
from mt5_mcp_trading.mt5_adapter.metatrader_parsing import parse_dataframe_csv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

TICKETS = {171648990, 171649461}
FROM_DATE = "2026-08-03"  # same window the live-run monitor already used

_logger = get_logger("mt5_mcp_trading.scripts.check_two_skipped_grid_tickets")


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

        raw = await client.call_tool("get_orders", {"from_date": FROM_DATE})
        rows = parse_dataframe_csv(raw)

    print(f"\n=== get_orders history from {FROM_DATE}: {len(rows)} total row(s) ===")
    matched = [r for r in rows if int(float(r["ticket"])) in TICKETS]
    if not matched:
        print(f"  none of {sorted(TICKETS)} found in order history at all")
    for row in matched:
        print(
            f"  ticket={row['ticket']} state={row['state']!r} type={row['type']!r} "
            f"volume_initial={row.get('volume_initial')} volume_current={row.get('volume_current')} "
            f"time_setup={row.get('time_setup')} time_done={row.get('time_done')}"
        )
    print("=====================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
