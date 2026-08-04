#!/usr/bin/env python3
"""
One-off recovery for the fifth live loop run's cycle 4: ticket 171653006 (BUY MARKET,
magic=72101 locally, strategy="runner") was placed successfully (retcode 10009) but its
mandatory SL/TP attach was rejected -- retcode 10016, "Invalid stops" -- the same
live-confirmed retcode-trust bug already root-caused in Step 28 (tickets 171617865, 171647565,
171651880): the tool's own message text claimed success while the real retcode said otherwise.
Per the established design, this correctly left the position OPEN_UNPROTECTED with no automatic
retry or close attempted, and correctly stopped the loop after cycle 4 (no error tolerance) --
recovery is a separate, explicitly-approved action, which is what this script is. User approved
closing this specific ticket.

Narrowly-scoped, one-off: acts on ONLY this one hardcoded ticket, nothing else, ever -- not a
general-purpose close tool. Same safety pattern as every prior unprotected-position recovery
script (Step 19, Step 27): re-verify live state immediately before acting, abort if it doesn't
match the premise, exactly one close_position() attempt, no retry, verify absence afterward.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Re-verifies TICKET is a live position immediately before acting; aborts with no action if not.
- Exactly ONE close_position() attempt, no retry.
- Verifies the position is ABSENT afterward (from get_positions()).
- Places no new order of any kind.
- Never reads, logs, or prints .env or any credential.
"""

from __future__ import annotations

import asyncio
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
TICKET = 171653006  # OPEN_UNPROTECTED runner position, SL/TP attach rejected (retcode 10016)


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Closing unprotected position {TICKET} -- one attempt, no retry, no new order ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        print(f"  state_store.lookup({TICKET}) = {state_store.lookup(TICKET)}")

        positions_before = await account.get_positions(symbol=SYMBOL)
        target_present = any(p.ticket == TICKET for p in positions_before)
        print(f"\n=== BEFORE: live positions on {SYMBOL} (all magics): {len(positions_before)} ===")
        for p in positions_before:
            print(f"  {p}")
        print(f"=== target ({TICKET}) present as a position: {target_present} ===")

        if not target_present:
            print(f"\nABORT: ticket {TICKET} is not a live position -- refusing to proceed on an "
                  f"inconsistent premise. No action attempted.")
            return

        print(f"\n=== close_position({TICKET}) -- ONE attempt, no retry ===")
        try:
            result = await executor.close_position(TICKET)
            print(result)
        except Exception as exc:
            print(f"close_position({TICKET}) raised: {exc!r}")
            result = None

        positions_after = await account.get_positions(symbol=SYMBOL)
        target_still_present = any(p.ticket == TICKET for p in positions_after)
        print(f"\n=== AFTER: live positions on {SYMBOL} (all magics): {len(positions_after)} ===")
        for p in positions_after:
            print(f"  {p}")
        print(f"=== target still present: {target_still_present} ===")
        print(f"  state_store.lookup({TICKET}) = {state_store.lookup(TICKET)}")

        passed = not target_still_present
        print(f"\n=== {'PASSED' if passed else 'FAILED'}: ticket {TICKET} resolved: {passed} ===")

    print("\n=== Done. No new order placed. ===")


if __name__ == "__main__":
    asyncio.run(main())
