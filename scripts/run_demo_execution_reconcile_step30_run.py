#!/usr/bin/env python3
"""
Read-only + local-reconciliation-only script for the sixth live loop run (2026-08-04, Step 30):
9 tickets were created across 3 completed cycles before the loop stopped itself on cycle 3's
runner leg (retcode 10016 SL/TP-attach rejection, ticket 171654324, left OPEN_UNPROTECTED).

For each of the 8 protected tickets (171654091, 171654092, 171654093, 171654190, 171654191,
171654192, 171654322, 171654323): if confirmed absent from live positions/orders, reconciles the
local StateStore record to CLOSED (no MCP call -- nothing left on the broker side to act on). If
still live, leaves it untouched (matches this project's standing "no cleanup, leave protected
results open" design).

171654324 (OPEN_UNPROTECTED, unresolved retcode-10016 case) is deliberately NOT touched or
reconciled here -- recovery for an unprotected position is always a separate, explicitly-approved
action (see docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md and every prior recovery script
this project has used). This script only reports its current live state.

Places, modifies, and cancels NO order. The only mutation possible is StateStore.record_closed()
for tickets independently confirmed absent from the broker, which is local bookkeeping only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime, timezone
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
PROTECTED_TICKETS = (
    171654091, 171654092, 171654093,
    171654190, 171654191, 171654192,
    171654322, 171654323,
)
UNPROTECTED_TICKET = 171654324  # left OPEN_UNPROTECTED -- NOT touched, report only


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses ===")

        positions = await account.get_positions(symbol=SYMBOL)
        orders = await account.get_orders(symbol=SYMBOL)
        pos_by_ticket = {p.ticket: p for p in positions}
        ord_by_ticket = {o.ticket: o for o in orders}

        print(f"\n=== Live positions on {SYMBOL} (all magics): {len(positions)} ===")
        for p in positions:
            print(f"  {p}")
        print(f"=== Live pending orders on {SYMBOL} (all magics): {len(orders)} ===")
        for o in orders:
            print(f"  {o}")

        print(f"\n=== Unprotected ticket {UNPROTECTED_TICKET} -- NOT touched, report only ===")
        if UNPROTECTED_TICKET in pos_by_ticket:
            print(f"  STILL LIVE as a position: {pos_by_ticket[UNPROTECTED_TICKET]}")
        elif UNPROTECTED_TICKET in ord_by_ticket:
            print(f"  STILL LIVE as a pending order: {ord_by_ticket[UNPROTECTED_TICKET]}")
        else:
            print(f"  absent from live positions/orders (resolved outside this process)")
        print(f"  local: {state_store.lookup(UNPROTECTED_TICKET)}")

        print(f"\n=== Reconciling protected tickets found absent (local-state only, no MCP call) ===")
        reconciled = []
        still_live = []
        for t in PROTECTED_TICKETS:
            live = t in pos_by_ticket or t in ord_by_ticket
            if live:
                still_live.append(t)
                print(f"  {t}: still live, left untouched")
                continue
            try:
                state_store.record_closed(
                    t,
                    reason="confirmed absent from live positions/orders -- closed outside this "
                           "process (no close_position()/cancel() call was ever made for it), "
                           "most likely filled/triggered then closed via its own broker-side "
                           "SL/TP",
                    closed_at=datetime.now(timezone.utc),
                )
                reconciled.append(t)
                print(f"  {t}: absent -- reconciled to CLOSED")
            except Exception as exc:
                print(f"  {t}: record_closed() raised: {exc!r}")

        print(f"\n=== Summary: {len(reconciled)} reconciled, {len(still_live)} still live "
              f"(left untouched), unprotected ticket {UNPROTECTED_TICKET} not acted on ===")
        print(f"  reconciled: {reconciled}")
        print(f"  still live: {still_live}")

    print("\n=== Done. No MCP order call made, no new order placed, no unprotected-position "
          "action taken. ===")


if __name__ == "__main__":
    asyncio.run(main())
