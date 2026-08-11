#!/usr/bin/env python3
"""
One-off diagnostic (not part of Phase 9's scoped deliverables): fresh read-only reconciliation
check immediately after Step 7 run #10 finished its full, unbroken 1->30 cycles (the first ever
in this project's history) and self-stopped on max_cycles at 2026-08-11 16:14:27. Confirms real
live MT5 state and reports which local StateStore records are still OPEN/OPEN_UNPROTECTED so an
exact before/leaving-for-home-machine picture exists.

READ-ONLY: get_positions()/get_orders()/get_deals() only. No `executor` reference, no order of any
kind, no StateStore writes.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

SYMBOL = "BTCUSD"


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del executor  # deliberately never used -- read-only diagnostic

        positions = await account.get_positions(symbol=SYMBOL)
        orders = await account.get_orders(symbol=SYMBOL)
        deals = await McpDealHistoryReader(client).get_deals(
            from_date="2026-08-11", to_date="2026-08-12",
        )
        all_records = state_store.all_records()

    total_lots = sum(p.volume for p in positions) + sum(o.volume for o in orders)

    print(f"\n=== REAL current open positions on {SYMBOL}: {len(positions)} ===")
    for p in positions:
        print(f"  ticket={p.ticket} side={p.side} volume={p.volume} sl={p.sl} tp={p.tp} "
              f"magic(broker-reported)={p.magic}")

    print(f"\n=== REAL current pending orders on {SYMBOL}: {len(orders)} ===")
    for o in orders:
        print(f"  ticket={o.ticket} side={o.side} price={o.price} volume={o.volume} "
              f"magic(broker-reported)={o.magic}")

    print(f"\n=== Total live exposure: {total_lots:.2f} lots "
          f"({len(positions)} position(s) + {len(orders)} pending order(s)) ===")

    live_tickets = {p.ticket for p in positions} | {o.ticket for o in orders}
    stale_open = [r for r in all_records if r.status in ("OPEN", "OPEN_UNPROTECTED")]

    print(f"\n=== Local StateStore records still OPEN/OPEN_UNPROTECTED: {len(stale_open)} ===")
    by_position: dict[int, list] = {}
    for deal in deals:
        by_position.setdefault(deal.position_id, []).append(deal)

    still_live_count = 0
    todays_run10 = [r for r in stale_open if r.submitted_at.date().isoformat() == "2026-08-11"
                     and r.submitted_at.hour >= 6]
    for r in stale_open:
        live = r.ticket in live_tickets
        if live:
            still_live_count += 1
        matched = by_position.get(r.ticket, [])
        outs = [d for d in matched if d.entry in (1, 2)]
        verdict = "STILL LIVE (do not touch)" if live else (
            f"NOT LIVE, {len(outs)} closing deal(s) found -> should reconcile CLOSED"
            if outs else "NOT LIVE, no deal found -> should reconcile CANCELLED"
        )
        print(f"  ticket={r.ticket} status={r.status} submitted_at={r.submitted_at} "
              f"magic={getattr(r, 'magic', None)} -- {verdict}")

    print(f"\n=== Of {len(stale_open)} locally-OPEN records, {still_live_count} are confirmed "
          f"still live; {len(stale_open) - still_live_count} are stale bookkeeping ===")
    print(f"=== Records from run #10's own window (today, >=06:00 UTC): {len(todays_run10)} ===")
    print("=====================================================\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
