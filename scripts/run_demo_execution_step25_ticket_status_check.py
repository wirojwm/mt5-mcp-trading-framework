#!/usr/bin/env python3
"""
Read-only status check on the 6 tickets left open by Step 25's third live loop run
(safe midday stop after 6/12 cycles, 2026-08-04). Live state is known to move between a report
and any follow-up action (Step 9, Step 15, Step 20 precedent) -- this script exists solely to get
a fresh, accurate picture before deciding on any cleanup action, which is a separate, explicit
next step.

Places, modifies, and closes NOTHING. Read-only: get_positions()/get_orders() only.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

SYMBOL = "BTCUSD"

# The 6 tickets Step 25's loop run left open when it was stopped safely mid-run (cycles 5/6
# rejected by the exposure cap, so only 4 cycles' worth of grid + 1 runner position/1 runner
# pending survived to the stop). Not acted on here -- read-only report only.
GRID_POSITION_TICKETS = (171649460,)  # grid BUY, filled from its own pending order
RUNNER_POSITION_TICKETS = (171649631,)  # runner SELL
GRID_PENDING_TICKETS = (171648990, 171649324, 171649422, 171649461)


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses (read-only this run) ===")

        positions = await account.get_positions(symbol=SYMBOL)
        orders = await account.get_orders(symbol=SYMBOL)
        live_position_tickets = {p.ticket for p in positions}
        live_order_tickets = {o.ticket for o in orders}

        print(f"\n=== Live positions on {SYMBOL} (all magics): {len(positions)} ===")
        for p in positions:
            print(f"  {p}")
        print(f"=== Live pending orders on {SYMBOL} (all magics): {len(orders)} ===")
        for o in orders:
            print(f"  {o}")

        def classify(ticket: int) -> str:
            if ticket in live_position_tickets:
                return "OPEN position"
            if ticket in live_order_tickets:
                return "PENDING order"
            return "absent (closed/filled-and-closed/cancelled elsewhere, or stale local state)"

        all_six = (*GRID_POSITION_TICKETS, *RUNNER_POSITION_TICKETS, *GRID_PENDING_TICKETS)

        print(f"\n=== Step 25 grid position ticket ({len(GRID_POSITION_TICKETS)}) ===")
        for t in GRID_POSITION_TICKETS:
            print(f"  {t}: {classify(t)}  | local: {state_store.lookup(t)}")

        print(f"\n=== Step 25 runner position ticket ({len(RUNNER_POSITION_TICKETS)}) ===")
        for t in RUNNER_POSITION_TICKETS:
            print(f"  {t}: {classify(t)}  | local: {state_store.lookup(t)}")

        print(f"\n=== Step 25 grid pending tickets ({len(GRID_PENDING_TICKETS)}) ===")
        for t in GRID_PENDING_TICKETS:
            print(f"  {t}: {classify(t)}  | local: {state_store.lookup(t)}")

        still_live = [t for t in all_six if classify(t) != "absent (closed/filled-and-closed/cancelled elsewhere, or stale local state)"]
        print(f"\n=== Summary: {len(still_live)}/{len(all_six)} of Step 25's leftover tickets still live ===")

    print("\n=== Done. Read-only -- nothing placed, modified, or closed. ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
