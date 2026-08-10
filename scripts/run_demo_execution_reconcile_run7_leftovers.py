#!/usr/bin/env python3
"""
One-off reconciliation (not part of Phase 9's scoped deliverables): end-of-day reconciliation of
Live run #7's (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md) local StateStore records left "OPEN" after
the user's explicit stop-work order halted the run at cycle 3 (stop-file, clean exit, PID `6536`).
The read-only end-of-day pre-flight found the account fully flat (0 positions, 0 pending orders,
BTCUSD) -- all 7 of run #7's real tickets across its 3 cycles had already filled and subsequently
either closed (SL/TP) or (for the two never-filled grid LIMIT orders) remained pending until they
were superseded by the loop's own cancel-and-replace behavior before the stop.

READ-ONLY against MT5 (get_positions/get_orders/get_deals, all already READ_ONLY-classified) plus
LOCAL-ONLY StateStore writes (record_closed()/record_cancelled() never make an MCP call) -- no
`executor` reference, no order of any kind submitted or modified.
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
from mt5_mcp_trading.monitoring.live_performance import infer_deal_time_offset
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

SYMBOL = "BTCUSD"
RUN7_TICKETS = {
    171922884, 171922885, 171922886, 171922988, 171922989, 171923143, 171923144,
}

_logger = get_logger("mt5_mcp_trading.scripts.reconcile_run7_leftovers")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del executor  # deliberately never used -- reconciliation is local-write only

        live_positions = await account.get_positions(symbol=SYMBOL)
        live_orders = await account.get_orders(symbol=SYMBOL)
        deals = await McpDealHistoryReader(client).get_deals(
            from_date="2026-08-09", to_date="2026-08-11",
        )
        all_records = state_store.all_records()

        offset = infer_deal_time_offset(all_records, deals) or timedelta(0)
        _logger.info("inferred deal_time_offset=%s", offset)

        live_tickets = {p.ticket for p in live_positions} | {o.ticket for o in live_orders}
        by_position: dict[int, list] = {}
        for deal in deals:
            by_position.setdefault(deal.position_id, []).append(deal)

        run7_open = [
            r for r in all_records
            if r.status in ("OPEN", "OPEN_UNPROTECTED") and r.ticket in RUN7_TICKETS
        ]

        still_live: list[int] = []
        closed: list[tuple[int, datetime]] = []
        cancelled: list[int] = []

        for record in run7_open:
            if record.ticket in live_tickets:
                still_live.append(record.ticket)
                continue
            matched = by_position.get(record.ticket, [])
            outs = [d for d in matched if d.entry in (1, 2)]
            if outs:
                closed_at = max(d.time for d in outs) - offset
                closed.append((record.ticket, closed_at))
            else:
                cancelled.append(record.ticket)

        now = datetime.now(timezone.utc)
        for ticket, closed_at in closed:
            state_store.record_closed(
                ticket,
                reason="reconciled run #7 (end of day): filled deal confirmed, absent from live "
                       "state after the explicit stop-work halt at cycle 3",
                closed_at=closed_at,
            )
        for ticket in cancelled:
            state_store.record_cancelled(
                ticket,
                reason="reconciled run #7 (end of day): no deal found, absent from live pending "
                       "orders after the explicit stop-work halt at cycle 3",
                closed_at=now,
            )

    print(f"\n=== {len(run7_open)} local record(s) from run #7 were still OPEN/OPEN_UNPROTECTED ===")
    print(f"    still genuinely live, untouched: {sorted(still_live)}")
    print(f"    reconciled to CLOSED (real deal found): {sorted(t for t, _ in closed)}")
    print(f"    reconciled to CANCELLED (no deal found): {sorted(cancelled)}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
