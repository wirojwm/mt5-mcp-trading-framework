#!/usr/bin/env python3
"""
One-off reconciliation (not part of Phase 9's scoped deliverables): bulk reconciliation of the
130-record stale StateStore backlog flagged, but deliberately not touched, at Step 7 run #10's
end-of-day close-out (2026-08-11, work machine) -- 136 local records still OPEN/OPEN_UNPROTECTED,
of which 130 were already confirmed (read-only) not live, spanning 2026-08-03/04/07/11. This
session (2026-08-13, work machine, real var/order_state available) closes that backlog using the
same classify-by-deal-history pattern as every prior leftover-reconciliation script in this
project (run_demo_execution_reconcile_20260810_afternoon_leftovers.py, run6_leftovers.py,
run7_leftovers.py, the 2026-08-07 leftovers script) -- NOT date-scoped this time, since the whole
point is clearing the accumulated backlog across all of those dates in one bulk pass, not just
one day's.

For each locally OPEN/OPEN_UNPROTECTED ticket:
- Still a real live position or pending order -> left untouched entirely (expected to be a small
  number: whatever's genuinely still open right now).
- Not live, but has a matching OUT-entry Deal (filled/closed at some point, incl. manual closes
  and SL/TP fills) -> record_closed(), closed_at derived from the real deal time, corrected for
  Deal.time's confirmed +UTC mislabel via infer_deal_time_offset() (same fix already proven in
  the live-performance monitor and every prior reconcile script).
- Not live, no matching Deal at all (e.g. a grid LIMIT order that never filled, cancelled
  manually or by a run's own stop) -> record_cancelled(), closed_at = now.

READ-ONLY against MT5 (get_positions/get_orders/get_deals, all already READ_ONLY-classified) plus
LOCAL-ONLY StateStore writes (record_closed()/record_cancelled() never make an MCP call) -- no
`executor` reference anywhere in this file, no order of any kind submitted, modified, cancelled,
or closed by this script.
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

_logger = get_logger("mt5_mcp_trading.scripts.reconcile_20260813_backlog")


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

        all_records = state_store.all_records()
        stale_open = [r for r in all_records if r.status in ("OPEN", "OPEN_UNPROTECTED")]
        earliest_submitted = min((r.submitted_at for r in stale_open), default=None)
        from_date = (earliest_submitted or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc)
        to_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        _logger.info(
            "Found %d locally OPEN/OPEN_UNPROTECTED record(s); requesting real deal history "
            "from %s to %s ...", len(stale_open), from_date, to_date,
        )

        deals = await McpDealHistoryReader(client).get_deals(from_date=from_date, to_date=to_date)
        _logger.info("Got %d real deal(s) from get_deals.", len(deals))

        offset = infer_deal_time_offset(all_records, deals) or timedelta(0)
        _logger.info("inferred deal_time_offset=%s", offset)

        live_tickets = {p.ticket for p in live_positions} | {o.ticket for o in live_orders}
        by_position: dict[int, list] = {}
        for deal in deals:
            by_position.setdefault(deal.position_id, []).append(deal)

        still_live: list[int] = []
        closed: list[tuple[int, datetime]] = []
        cancelled: list[int] = []

        for record in stale_open:
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

        for ticket, closed_at in closed:
            state_store.record_closed(
                ticket,
                reason="reconciled 2026-08-13 backlog pass: real deal found, no longer live",
                closed_at=closed_at,
            )
        for ticket in cancelled:
            state_store.record_cancelled(
                ticket,
                reason="reconciled 2026-08-13 backlog pass: no deal found, no longer live "
                       "(unfilled/cancelled pending)",
                closed_at=now,
            )

    print(f"\n=== {len(stale_open)} local record(s) were OPEN/OPEN_UNPROTECTED before this pass ===")
    print(f"    still genuinely live, untouched ({len(still_live)}): {sorted(still_live)}")
    print(f"    reconciled to CLOSED (real deal found) ({len(closed)}): "
          f"{sorted(t for t, _ in closed)}")
    print(f"    reconciled to CANCELLED (no deal found) ({len(cancelled)}): {sorted(cancelled)}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
