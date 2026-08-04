#!/usr/bin/env python3
"""
Pure read-only reconciliation of the 5 pending grid orders Step 30 left open
(171654092, 171654190, 171654191, 171654322, 171654323). User reported manually cancelling all 5
directly in the MT5 terminal, outside this project's code. This script only reads live
positions/orders and local StateStore records to confirm current state -- it does NOT write
anything to StateStore and makes NO MCP order call of any kind (no place/modify/cancel/close).

Places, modifies, cancels, and closes NOTHING. Writes nothing to local state either. Pure
read-only report.
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
TICKETS = (171654092, 171654190, 171654191, 171654322, 171654323)


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
        pos_tickets = {p.ticket for p in positions}
        ord_tickets = {o.ticket for o in orders}

        print(f"\n=== Live positions on {SYMBOL} (all magics): {len(positions)} ===")
        for p in positions:
            print(f"  {p}")
        print(f"=== Live pending orders on {SYMBOL} (all magics): {len(orders)} ===")
        for o in orders:
            print(f"  {o}")

        print(f"\n=== Per-ticket check (read-only, no writes) ===")
        for t in TICKETS:
            live = "LIVE POSITION" if t in pos_tickets else "LIVE PENDING ORDER" if t in ord_tickets else "absent"
            local = state_store.lookup(t)
            local_status = local.status if local is not None else "NO LOCAL RECORD"
            print(f"  {t}: live={live}  local_status={local_status}")

    print("\n=== Done. Pure read-only -- nothing placed, modified, cancelled, closed, or written "
          "to local state. ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
