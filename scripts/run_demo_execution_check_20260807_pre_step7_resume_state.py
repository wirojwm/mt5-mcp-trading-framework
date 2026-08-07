#!/usr/bin/env python3
"""
One-off diagnostic (not part of Phase 9's scoped deliverables): fresh read-only state check before
resuming Step 7's first live attempt (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md) after the lunch-break
stop. Live run #1 left 1 open runner position + 4 pending grid orders (0.05 lots) as of the stop --
this confirms whether that's still true or whether something closed on its own over the break.

READ-ONLY: get_positions()/get_orders() only. No `executor` reference, no order of any kind.
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


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del client, state_store  # deliberately unused -- read-only, live-account-only report
        del executor  # deliberately never used -- read-only diagnostic

        positions = await account.get_positions(symbol=SYMBOL)
        orders = await account.get_orders(symbol=SYMBOL)

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
    print("=====================================================\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
