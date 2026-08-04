#!/usr/bin/env python3
"""
Read-only status check after the first full-duration bounded-autonomous-loop run (12/12 cycles,
2026-08-04). That run submitted 36 tickets (24 grid LIMIT orders across 12 cycles, magic=71101;
12 runner MARKET positions, magic=72101), all left open by the loop's designed no-cleanup
behavior. Live state is known to move between a report and any follow-up action (Step 9, Step 15
precedent) -- this script exists solely to get a fresh, accurate picture before deciding on any
cleanup action, which is a separate, explicit next step.

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

# All tickets the loop run submitted this session, by magic/side, for cross-referencing against
# live state below. Not acted on here -- read-only report only.
GRID_TICKETS = (
    171644242, 171644243, 171644332, 171644333, 171644504, 171644505,
    171644689, 171644691, 171644795, 171644796, 171644862, 171644863,
    171644907, 171644908, 171644954, 171644955, 171645010, 171645011,
    171645070, 171645071, 171645204, 171645207, 171645569, 171645570,
)
RUNNER_TICKETS = (
    171644244, 171644334, 171644506, 171644692, 171644797, 171644865,
    171644909, 171644956, 171645012, 171645073, 171645208, 171645571,
)
PRE_EXISTING_TICKETS = (171622543, 171622789, 171622791)  # from before this loop run, out of scope


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

        print(f"\n=== Grid tickets from this loop run ({len(GRID_TICKETS)}) ===")
        for t in GRID_TICKETS:
            print(f"  {t}: {classify(t)}  | local: {state_store.lookup(t)}")

        print(f"\n=== Runner tickets from this loop run ({len(RUNNER_TICKETS)}) ===")
        for t in RUNNER_TICKETS:
            print(f"  {t}: {classify(t)}  | local: {state_store.lookup(t)}")

        print(f"\n=== Pre-existing tickets (out of scope, not from this run) ===")
        for t in PRE_EXISTING_TICKETS:
            print(f"  {t}: {classify(t)}  | local: {state_store.lookup(t)}")

        still_live = [t for t in (*GRID_TICKETS, *RUNNER_TICKETS) if classify(t) != "absent (closed/filled-and-closed/cancelled elsewhere, or stale local state)"]
        print(f"\n=== Summary: {len(still_live)}/{len(GRID_TICKETS) + len(RUNNER_TICKETS)} "
              f"of this loop run's tickets still live ===")

    print("\n=== Done. Read-only -- nothing placed, modified, or closed. ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
