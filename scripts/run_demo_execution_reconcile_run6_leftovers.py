#!/usr/bin/env python3
"""
One-off reconciliation (not part of Phase 9's scoped deliverables): reconciles Live run #6's
(docs/PHASE9_FORWARD_TEST_CHECKPOINT.md) local StateStore records left "OPEN"/"OPEN_UNPROTECTED"
after cycle 11 tripped MANAGE_ONLY (unknown_real=(171922069,), root-caused in
run_demo_execution_investigate_ticket_171922069.py as MT5's own transient SL-execution order, same
structural gap as run #4's 171909600 incident) and the loop halted itself per its "no error
tolerance" design.

The read-only pre-flight (run_demo_execution_check_20260810_afternoon_preflight.py, re-run after
the halt) found the account had ALREADY gone fully flat again on its own (0 positions, 0 pending
orders, BTCUSD) -- every one of run #6's 12 real tickets already has a matching closing deal, none
left genuinely open or merely cancelled-unfilled. This script applies that same classification and
writes it locally.

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
RUN6_TICKETS = {
    171920075, 171920076, 171920077, 171920233, 171920234, 171920424, 171920425,
    171921171, 171921172, 171921298, 171921299, 171922067, 171922068,
}

_logger = get_logger("mt5_mcp_trading.scripts.reconcile_run6_leftovers")


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

        run6_open = [
            r for r in all_records
            if r.status in ("OPEN", "OPEN_UNPROTECTED") and r.ticket in RUN6_TICKETS
        ]

        still_live: list[int] = []
        closed: list[tuple[int, datetime]] = []
        cancelled: list[int] = []

        for record in run6_open:
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
                reason="reconciled run #6: filled deal confirmed, absent from live state after "
                       "cycle 11's MANAGE_ONLY halt (ticket 171922069 root-caused as a transient "
                       "MT5 SL-execution artifact)",
                closed_at=closed_at,
            )
        for ticket in cancelled:
            state_store.record_cancelled(
                ticket,
                reason="reconciled run #6: no deal found, absent from live pending orders after "
                       "cycle 11's MANAGE_ONLY halt",
                closed_at=now,
            )

    print(f"\n=== {len(run6_open)} local record(s) from run #6 were still OPEN/OPEN_UNPROTECTED ===")
    print(f"    still genuinely live, untouched: {sorted(still_live)}")
    print(f"    reconciled to CLOSED (real deal found): {sorted(t for t, _ in closed)}")
    print(f"    reconciled to CANCELLED (no deal found): {sorted(cancelled)}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
