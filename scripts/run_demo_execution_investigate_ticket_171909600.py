#!/usr/bin/env python3
"""
One-off diagnostic (not part of Phase 9's scoped deliverables): investigates why Live run #4's
cycle 10 (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md) tripped MANAGE_ONLY posture with
unknown_real=(171909600,) -- a real MT5-side ticket with zero matching local StateStore record.
Immediately preceding it in the same cycle, grid ticket 171909599 (BUY LIMIT, magic=71101)
placed successfully and IS recorded locally.

READ-ONLY ONLY: get_all_positions / get_all_pending_orders / get_orders (history) / get_deals
(history) -- all already READ_ONLY-classified in mcp_adapter/metatrader_tools.py. No `executor`
reference anywhere. Places, modifies, cancels, or closes nothing.

Deliberately queries account-WIDE (no symbol filter), matching exactly what
mt5_adapter/mcp_order_executor.py's real reconcile() call does (account.get_positions()/
get_orders() with symbol=None) -- ticket 171909600 could in principle be on any symbol, not
just BTCUSD, since that is the actual scope reconcile() checks against.
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
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.metatrader_parsing import parse_dataframe_csv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

TICKETS = {171909599, 171909600}
# Cycle 10 submitted at 2026-08-10T02:43:26 UTC (from the pipeline log) -- wide margin both sides.
FROM_DATE = "2026-08-09"
TO_DATE = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

_logger = get_logger("mt5_mcp_trading.scripts.investigate_ticket_171909600")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del executor  # deliberately never used -- read-only diagnostic, no order of any kind

        # 1) Current live snapshot, account-wide (matches reconcile()'s real scope exactly).
        positions = await account.get_positions()
        orders = await account.get_orders()

        # 2) Order history (pending-order lifecycle: placed/filled/cancelled/expired).
        raw_order_hist = await client.call_tool(
            "get_orders", {"from_date": FROM_DATE, "to_date": TO_DATE}
        )
        order_hist_rows = parse_dataframe_csv(raw_order_hist)

        # 3) Deal history (actual executions only).
        raw_deals = await client.call_tool(
            "get_deals", {"from_date": FROM_DATE, "to_date": TO_DATE}
        )
        deal_rows = parse_dataframe_csv(raw_deals)

        # 4) Local state, both tickets plus everything currently open (for full context).
        local_open = state_store.all_open()
        local_all = state_store.all_records()

    print(f"\n=== CURRENT live positions (account-wide, no symbol filter): {len(positions)} ===")
    for p in positions:
        marker = " <== TARGET" if p.ticket in TICKETS else ""
        print(f"  ticket={p.ticket} symbol={p.symbol} side={p.side} volume={p.volume} "
              f"open={p.price_open} sl={p.sl} tp={p.tp} "
              f"magic(broker-reported)={p.magic}{marker}")

    print(f"\n=== CURRENT live pending orders (account-wide, no symbol filter): {len(orders)} ===")
    for o in orders:
        marker = " <== TARGET" if o.ticket in TICKETS else ""
        print(f"  ticket={o.ticket} symbol={o.symbol} side={o.side} price={o.price} "
              f"volume={o.volume} magic(broker-reported)={o.magic}{marker}")

    print(f"\n=== get_orders HISTORY from {FROM_DATE} to {TO_DATE}: "
          f"{len(order_hist_rows)} total row(s), showing matches only ===")
    matched_hist = [r for r in order_hist_rows if int(float(r["ticket"])) in TICKETS]
    if not matched_hist:
        print(f"  none of {sorted(TICKETS)} found in order history at all")
    for row in matched_hist:
        print(
            f"  ticket={row['ticket']} symbol={row.get('symbol')} state={row.get('state')!r} "
            f"type={row.get('type')!r} volume_initial={row.get('volume_initial')} "
            f"volume_current={row.get('volume_current')} price_open={row.get('price_open')} "
            f"time_setup={row.get('time_setup')} time_done={row.get('time_done')} "
            f"magic={row.get('magic')} comment={row.get('comment')}"
        )

    print(f"\n=== get_deals HISTORY from {FROM_DATE} to {TO_DATE}: "
          f"{len(deal_rows)} total row(s), showing matches only (by ticket, order, or position_id) ===")
    matched_deals = [
        r for r in deal_rows
        if int(float(r.get("ticket", -1) or -1)) in TICKETS
        or int(float(r.get("order", -1) or -1)) in TICKETS
        or int(float(r.get("position_id", -1) or -1)) in TICKETS
    ]
    if not matched_deals:
        print(f"  none of {sorted(TICKETS)} found in deal history at all (as ticket/order/position_id)")
    for row in matched_deals:
        print(
            f"  deal_ticket={row.get('ticket')} order={row.get('order')} "
            f"position_id={row.get('position_id')} symbol={row.get('symbol')} "
            f"type={row.get('type')!r} entry={row.get('entry')!r} volume={row.get('volume')} "
            f"price={row.get('price')} profit={row.get('profit')} time={row.get('time')} "
            f"magic={row.get('magic')} comment={row.get('comment')}"
        )

    print("\n=== LOCAL StateStore ===")
    for t in sorted(TICKETS):
        rec = next((r for r in local_all if r.ticket == t), None)
        if rec is None:
            print(f"  ticket={t}: NO local record of any status")
        else:
            print(f"  ticket={t}: status={rec.status} strategy={rec.strategy} magic={rec.magic} "
                  f"submitted_at={rec.submitted_at} closed_at={rec.closed_at}")

    print(f"\n=== Full local OPEN/OPEN_UNPROTECTED set for context: {len(local_open)} record(s) "
          f"(not printed individually -- see var/order_state/*.json if needed) ===")

    print("\n=====================================================")
    print("READ-ONLY diagnostic complete. No order, modification, cancellation, or close performed.")
    print("=====================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
