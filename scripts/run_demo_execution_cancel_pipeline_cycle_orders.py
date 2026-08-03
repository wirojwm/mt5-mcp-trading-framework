#!/usr/bin/env python3
"""
Pipeline-wiring cleanup: cancels the two real pending LIMIT orders placed by the first live
run of scripts/run_demo_execution_pipeline_cycle.py (STRATEGY="GRID", 2026-08-03) -- tickets
171621248 (BUY_LIMIT) and 171621249 (SELL_LIMIT), both magic=71101/strategy="grid". One-off,
narrowly-scoped script: cancels ONLY these two hardcoded tickets, nothing else, ever -- not a
general-purpose cancel tool.

Run only once, with explicit approval, reviewed live.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Verifies both tickets are present in live pending orders (account.get_orders(symbol=SYMBOL),
  deliberately UNFILTERED by magic) BEFORE attempting anything. Filtering by magic here would
  be misleading: MT5 is confirmed to always report magic=0 on orders this project places (see
  docs/mcp_tool_classification.md item 7), the same reason McpOrderExecutor's own
  _verify_present() queries get_orders() without a magic filter. Aborts, with no cancel attempt
  on either ticket, if either is missing beforehand.
- Exactly ONE cancel_pending_order attempt per ticket via McpOrderExecutor.cancel() -- no retry
  of either, regardless of outcome (matches this project's established "one attempt, no retry"
  convention for every live trading call). One ticket's cancel raising is caught and reported,
  not allowed to prevent the other, independent ticket's cancel from being attempted.
- Verifies both tickets are ABSENT from live pending orders afterward.
- Places no new order of any kind -- cancel-only.
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state"  # directory: one <ticket>.json file per ticket

SYMBOL = "BTCUSD"
TICKETS = (171621248, 171621249)  # the exact two, and only these two, tickets to cancel


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Cancelling ONLY tickets={TICKETS} -- one attempt each, no retry, no new order ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        orders_before = await account.get_orders(symbol=SYMBOL)  # unfiltered by magic -- see SAFETY
        present_before = {t: any(o.ticket == t for o in orders_before) for t in TICKETS}
        print(f"\n=== BEFORE: live pending orders on {SYMBOL} (all magics): {len(orders_before)} ===")
        for o in orders_before:
            print(f"  {o}")
        print(f"=== Target tickets present before cancel: {present_before} ===")
        for ticket in TICKETS:
            print(f"  state_store.lookup({ticket}) = {state_store.lookup(ticket)}")

        missing = [t for t, present in present_before.items() if not present]
        if missing:
            print(f"\nABORT: ticket(s) {missing} not found in live pending orders -- refusing "
                  f"to proceed on an inconsistent premise. No cancel attempted for any ticket.")
            return

        results = {}
        errors = {}
        for ticket in TICKETS:
            print(f"\n=== cancel({ticket}) -- ONE attempt, no retry ===")
            try:
                result = await executor.cancel(ticket)
            except Exception as exc:
                print(f"cancel({ticket}) raised: {exc!r}")
                errors[ticket] = exc
                continue
            print(result)
            results[ticket] = result

        orders_after = await account.get_orders(symbol=SYMBOL)
        present_after = {t: any(o.ticket == t for o in orders_after) for t in TICKETS}
        print(f"\n=== AFTER: live pending orders on {SYMBOL} (all magics): {len(orders_after)} ===")
        for o in orders_after:
            print(f"  {o}")
        print(f"=== Target tickets present after cancel: {present_after} ===")
        for ticket in TICKETS:
            print(f"  state_store.lookup({ticket}) = {state_store.lookup(ticket)}")

        retcodes = {ticket: (results[ticket].retcode if ticket in results else None)
                    for ticket in TICKETS}
        print(f"\n=== Retcodes: {retcodes} ===")
        if errors:
            print(f"=== Raised (not a retcode -- the call itself failed): "
                  f"{ {t: repr(e) for t, e in errors.items()} } ===")

        both_absent = not any(present_after.values())
        print(f"\n=== {'PASSED' if both_absent else 'FAILED'}: both tickets absent from live "
              f"pending orders after cancel: {both_absent} ===")

    print("\n=== Done. No new order placed. ===")


if __name__ == "__main__":
    asyncio.run(main())
