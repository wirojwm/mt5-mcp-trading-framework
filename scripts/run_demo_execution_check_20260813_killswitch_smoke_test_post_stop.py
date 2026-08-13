#!/usr/bin/env python3
"""
One-off diagnostic (not part of Phase 9's scoped deliverables): fresh read-only state check
immediately after the 2026-08-13 kill-switch smoke test self-stopped
(`Stopping before cycle 9: daily loss limit breached (realized_pnl_since_reset=-0.04 breaches
max_daily_loss=0.01)`, `Done. 8 cycle(s) run.`). Confirms real live MT5 state and identifies which
ticket(s) actually closed to produce the real -0.04 loss the kill-switch reacted to (this project's
own pipeline_loop.py never logs broker-side SL/TP closes explicitly -- only get_deals proves it).

READ-ONLY: get_positions()/get_orders()/get_deals() only. No `executor` reference, no order of any
kind, no StateStore writes.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.live_performance import infer_deal_time_offset
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

SYMBOL = "BTCUSD"

# The 6 tickets this smoke test submitted (from its own stdout log) -- checked explicitly for
# whether each is still live or has closed.
SMOKE_TEST_TICKETS = {
    172109372: "grid BUY LIMIT (cycle 1)",
    172109373: "grid SELL LIMIT (cycle 1)",
    172109374: "runner MARKET BUY (cycle 1)",
    172109669: "grid BUY LIMIT (cycle 2)",
    172109670: "grid SELL LIMIT (cycle 2)",
    172109797: "grid BUY LIMIT (cycle 3)",
    172109798: "grid SELL LIMIT (cycle 3)",
    172110787: "grid BUY LIMIT (cycle 7)",
    172110790: "grid SELL LIMIT (cycle 7)",
}


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
            from_date="2026-08-13", to_date="2026-08-14",
        )
        all_records = state_store.all_records()
        offset = infer_deal_time_offset(all_records, deals) or timedelta(0)

    total_lots = sum(p.volume for p in positions) + sum(o.volume for o in orders)

    print(f"\n=== REAL current open positions on {SYMBOL}: {len(positions)} ===")
    for p in positions:
        print(f"  ticket={p.ticket} side={p.side} volume={p.volume} sl={p.sl} tp={p.tp}")
    unprotected = [p for p in positions if not p.sl or not p.tp]

    print(f"\n=== REAL current pending orders on {SYMBOL}: {len(orders)} ===")
    for o in orders:
        print(f"  ticket={o.ticket} side={o.side} price={o.price} volume={o.volume}")

    print(f"\n=== Total live exposure: {total_lots:.2f} lots "
          f"({len(positions)} position(s) + {len(orders)} pending order(s)) ===")
    print(f"=== Unprotected open positions (missing SL or TP): {len(unprotected)} ===")
    for p in unprotected:
        print(f"  ticket={p.ticket} side={p.side} volume={p.volume} sl={p.sl} tp={p.tp}")

    live_tickets = {p.ticket for p in positions} | {o.ticket for o in orders}
    by_position: dict[int, list] = {}
    for deal in deals:
        by_position.setdefault(deal.position_id, []).append(deal)

    print(f"\n=== Smoke test's own {len(SMOKE_TEST_TICKETS)} tickets, live vs. closed ===")
    for ticket, label in SMOKE_TEST_TICKETS.items():
        live = ticket in live_tickets
        matched = by_position.get(ticket, [])
        outs = [d for d in matched if d.entry in (1, 2)]
        if live:
            verdict = "STILL LIVE"
        elif outs:
            closed_at = max(d.time for d in outs) - offset
            pnl = sum(d.profit for d in outs)
            verdict = f"CLOSED, real deal found, pnl={pnl:+.2f}, closed_at(corrected)={closed_at.isoformat()}"
        else:
            verdict = "not live, no deal found (likely still-unfilled cancelled/expired grid LIMIT)"
        print(f"  ticket={ticket} ({label}): {verdict}")

    print(f"\n=== All real deals today (2026-08-13, get_deals from_date/to_date margin) ===")
    for d in sorted(deals, key=lambda x: x.time):
        print(f"  deal_ticket={d.ticket} position_id={d.position_id} order={d.order} "
              f"entry={d.entry} profit={d.profit:+.2f} time={d.time.isoformat()} "
              f"comment={d.comment!r}")

    print("=====================================================\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
