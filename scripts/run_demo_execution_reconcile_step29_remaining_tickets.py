#!/usr/bin/env python3
"""
Read-only reconciliation of the 5 tickets Step 29 (docs/PIPELINE_WIRING_CHECKPOINT.md) left open:
1 runner position (171652732) and 4 pending grid orders (171652730, 171652797, 171652844,
171653004). Cross-references live positions/orders against local StateStore records to confirm
ticket/symbol/side/volume/SL/TP/magic/local-state ownership for each -- no cleanup decision is
made or acted on here.

Places, modifies, cancels, and closes NOTHING. Read-only: get_positions()/get_orders()/
state_store.lookup() only.
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
TICKETS = (171652732, 171652730, 171652797, 171652844, 171653004)


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
        pos_by_ticket = {p.ticket: p for p in positions}
        ord_by_ticket = {o.ticket: o for o in orders}

        print(f"\n=== Live positions on {SYMBOL} (all magics): {len(positions)} ===")
        for p in positions:
            print(f"  {p}")
        print(f"=== Live pending orders on {SYMBOL} (all magics): {len(orders)} ===")
        for o in orders:
            print(f"  {o}")

        print(f"\n=== Reconciliation of Step 29's 5 remaining tickets ===")
        for t in TICKETS:
            local = state_store.lookup(t)
            if t in pos_by_ticket:
                live = pos_by_ticket[t]
                live_desc = (f"LIVE POSITION side={live.side} volume={live.volume} "
                             f"price_open={live.price_open} sl={live.sl} tp={live.tp} "
                             f"broker_magic={live.magic} profit={live.profit}")
            elif t in ord_by_ticket:
                live = ord_by_ticket[t]
                live_desc = (f"LIVE PENDING ORDER side={live.side} volume={live.volume} "
                             f"price={live.price} broker_magic={live.magic}")
            else:
                live_desc = "ABSENT from live positions/orders"
            print(f"\n  ticket={t}")
            print(f"    live:  {live_desc}")
            print(f"    local: {local}")

    print("\n=== Done. Read-only -- nothing placed, modified, cancelled, or closed. ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
