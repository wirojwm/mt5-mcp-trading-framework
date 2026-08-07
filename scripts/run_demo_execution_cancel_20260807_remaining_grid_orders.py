#!/usr/bin/env python3
"""
One-off cleanup: cancels the 3 real pending grid LIMIT orders left on the account after Phase 9
Step 5's live smoke test + reconciliation (2026-08-07) -- tickets 171809875, 171812719, 171812840,
all magic=71101/strategy="grid". One-off, narrowly-scoped script: cancels ONLY these three
hardcoded tickets, nothing else, ever -- not a general-purpose cancel tool. Mirrors
run_demo_execution_cancel_pipeline_cycle_orders.py's exact shape.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Verifies all three tickets are present in live pending orders (account.get_orders(symbol=SYMBOL),
  unfiltered by magic -- MT5 always reports magic=0 on orders this project places) BEFORE
  attempting anything. Aborts, with no cancel attempt on any ticket, if any is missing beforehand.
- Exactly ONE cancel_pending_order attempt per ticket via McpOrderExecutor.cancel() -- no retry of
  any, regardless of outcome. One ticket's cancel raising is caught and reported, not allowed to
  prevent the others' independent cancels from being attempted.
- McpOrderExecutor.cancel() already writes local state (record_cancelled()) itself, only after a
  confirmed-done retcode AND a fresh live read agree the ticket is genuinely absent -- no manual
  reconciliation needed here.
- Verifies all three tickets are ABSENT from live pending orders afterward.
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

SYMBOL = "BTCUSD"
TICKETS = (171809875, 171812719, 171812840)  # the exact three, and only these, tickets to cancel


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

        all_absent = not any(present_after.values())
        print(f"\n=== {'PASSED' if all_absent else 'FAILED'}: all three tickets absent from live "
              f"pending orders after cancel: {all_absent} ===")

    print("\n=== Done. No new order placed. ===")


if __name__ == "__main__":
    asyncio.run(main())
