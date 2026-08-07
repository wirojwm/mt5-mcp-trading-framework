#!/usr/bin/env python3
"""
Follow-up to the 2026-08-07 re-run of the Step 5 live smoke test, which was abruptly killed
mid-run (no clean shutdown, no "Done." log line) around cycle 11. Before deciding what's next,
this checks whether the get_deals fix actually worked in practice during that run -- i.e.
whether real realized P&L since today's reset boundary would have been computed correctly, and
whether the kill-switch SHOULD have tripped by the time the process died.

READ-ONLY: same demo_execution_session() pattern as every other diagnostic in this project,
`executor` deliberately discarded, no order of any kind.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.live_performance import infer_deal_time_offset, realized_pnl_since
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader
from mt5_mcp_trading.risk.daily_loss_guard import DailyLossLimitConfig, check_daily_loss_limit, daily_reset_boundary

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

CONFIG = DailyLossLimitConfig(max_daily_loss=0.01, reset_hour_utc=0)

_logger = get_logger("mt5_mcp_trading.scripts.check_rerun_kill_switch_correctness")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor  # deliberately never used -- read-only diagnostic

        now = datetime.now(timezone.utc)
        boundary = daily_reset_boundary(now, CONFIG.reset_hour_utc)
        fetch_from = (boundary - timedelta(days=1)).strftime("%Y-%m-%d")
        fetch_to = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        deals = await McpDealHistoryReader(client).get_deals(from_date=fetch_from, to_date=fetch_to)
        closed_records = state_store.all_closed()
        trusted_ids = {r.ticket for r in closed_records}

    offset = infer_deal_time_offset(closed_records, deals)
    applied_offset = offset if offset is not None else timedelta(0)
    pnl = realized_pnl_since(deals, since=boundary, trusted_position_ids=trusted_ids,
                              deal_time_offset=applied_offset)
    decision = check_daily_loss_limit(pnl, CONFIG)

    print(f"\n=== now={now.isoformat()} boundary={boundary.isoformat()} ===")
    print(f"=== fetched {len(deals)} deal(s) from {fetch_from} to {fetch_to} ===")
    print(f"=== {len(closed_records)} local closed record(s), {len(trusted_ids)} trusted ticket(s) ===")
    print(f"=== inferred deal_time_offset: {offset} (None means no MARKET-order reference found) ===")
    print(f"=== realized_pnl_since(boundary) = {pnl:.4f} ===")
    print(f"=== check_daily_loss_limit(max_daily_loss=0.01) -> approved={decision.approved} ===")
    print(f"    reasons: {decision.reasons}\n")


if __name__ == "__main__":
    asyncio.run(main())
