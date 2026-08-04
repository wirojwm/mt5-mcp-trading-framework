#!/usr/bin/env python3
"""
Resolves the 2 grid tickets Step 19 (docs/PIPELINE_WIRING_CHECKPOINT.md) left open on the demo
account: BUY_LIMIT 171647522 and SELL_LIMIT 171647525, magic=71101. Re-checked live immediately
before writing this script (not trusting Step 19's report, which is exactly the kind of drift
this project's cleanup scripts always re-verify against):

- 171647522: already ABSENT from live positions AND live pending orders -- most likely filled
  then closed via its own broker-side SL/TP, the same pattern observed repeatedly throughout this
  project (e.g. Step 17). Local StateStore still says OPEN (stale) since no close_position()/
  cancel() call was ever made for it by this project. Reconciled via StateStore.record_closed()
  directly -- no MCP call, nothing left on the broker side to act on.
- 171647525: still a live SELL_LIMIT pending order. Cancelled via McpOrderExecutor.cancel().

No active monitoring loop is running to manage these -- they exist only as residue from Step 19's
fix-verification cycles, not a real, ongoing strategy decision -- so the decision here is to
resolve both now rather than leave an unmonitored order on the account.

One-off, narrowly-scoped script: acts on ONLY these two hardcoded tickets, nothing else, ever.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Re-verifies live state for both tickets immediately before acting: TICKET_TO_RECONCILE must be
  ABSENT from live positions AND live pending orders; TICKET_TO_CANCEL must be a live pending
  order. Aborts, with no action attempted on ANY ticket, if either condition doesn't hold.
- Exactly ONE cancel() attempt for TICKET_TO_CANCEL, no retry. TICKET_TO_RECONCILE gets exactly
  one direct StateStore.record_closed() call, no MCP call at all.
- Verifies the cancel target is ABSENT afterward (from get_orders()).
- Places no new order of any kind.
- Never reads, logs, or prints .env or any credential.
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
TICKET_TO_RECONCILE = 171647522  # already absent live -- local state only, no MCP call
TICKET_TO_CANCEL = 171647525     # still a live SELL_LIMIT pending order


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Reconciling already-absent {TICKET_TO_RECONCILE}, cancelling live "
          f"{TICKET_TO_CANCEL} -- one attempt each, no retry, no new order ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        positions_before = await account.get_positions(symbol=SYMBOL)
        orders_before = await account.get_orders(symbol=SYMBOL)  # unfiltered by magic -- see docstring
        reconcile_target_absent = (
            not any(p.ticket == TICKET_TO_RECONCILE for p in positions_before)
            and not any(o.ticket == TICKET_TO_RECONCILE for o in orders_before)
        )
        cancel_target_present = any(o.ticket == TICKET_TO_CANCEL for o in orders_before)

        print(f"\n=== BEFORE: live positions on {SYMBOL} (all magics): {len(positions_before)} ===")
        for p in positions_before:
            print(f"  {p}")
        print(f"=== BEFORE: live pending orders on {SYMBOL} (all magics): {len(orders_before)} ===")
        for o in orders_before:
            print(f"  {o}")
        print(f"=== reconcile target ({TICKET_TO_RECONCILE}) absent from live positions/orders: "
              f"{reconcile_target_absent} ===")
        print(f"=== cancel target ({TICKET_TO_CANCEL}) present as a pending order: "
              f"{cancel_target_present} ===")
        for ticket in (TICKET_TO_RECONCILE, TICKET_TO_CANCEL):
            print(f"  state_store.lookup({ticket}) = {state_store.lookup(ticket)}")

        if not (reconcile_target_absent and cancel_target_present):
            print(f"\nABORT: live state doesn't match the premise this script was written "
                  f"against -- refusing to proceed on an inconsistent premise. No action "
                  f"attempted on ANY ticket.")
            return

        results = {}
        errors = {}

        print(f"\n=== Reconciling {TICKET_TO_RECONCILE} -- local StateStore only, no MCP call "
              f"(nothing left on the broker side to act on) ===")
        try:
            state_store.record_closed(
                TICKET_TO_RECONCILE,
                reason="confirmed absent from live positions/orders -- closed outside this "
                       "process (no close_position()/cancel() call was ever made for it), most "
                       "likely filled then closed via its own broker-side SL/TP",
                closed_at=datetime.now(timezone.utc),
            )
            print(f"state_store.lookup({TICKET_TO_RECONCILE}) = "
                  f"{state_store.lookup(TICKET_TO_RECONCILE)}")
        except Exception as exc:
            print(f"record_closed({TICKET_TO_RECONCILE}) raised: {exc!r}")
            errors[TICKET_TO_RECONCILE] = exc

        print(f"\n=== cancel({TICKET_TO_CANCEL}) -- ONE attempt, no retry ===")
        try:
            results[TICKET_TO_CANCEL] = await executor.cancel(TICKET_TO_CANCEL)
            print(results[TICKET_TO_CANCEL])
        except Exception as exc:
            print(f"cancel({TICKET_TO_CANCEL}) raised: {exc!r}")
            errors[TICKET_TO_CANCEL] = exc

        positions_after = await account.get_positions(symbol=SYMBOL)
        orders_after = await account.get_orders(symbol=SYMBOL)
        cancel_target_still_present = any(o.ticket == TICKET_TO_CANCEL for o in orders_after)

        print(f"\n=== AFTER: live positions on {SYMBOL} (all magics): {len(positions_after)} ===")
        for p in positions_after:
            print(f"  {p}")
        print(f"=== AFTER: live pending orders on {SYMBOL} (all magics): {len(orders_after)} ===")
        for o in orders_after:
            print(f"  {o}")
        print(f"=== cancel target present after: {cancel_target_still_present} ===")
        for ticket in (TICKET_TO_RECONCILE, TICKET_TO_CANCEL):
            print(f"  state_store.lookup({ticket}) = {state_store.lookup(ticket)}")

        retcode = results[TICKET_TO_CANCEL].retcode if TICKET_TO_CANCEL in results else None
        print(f"\n=== Retcode for cancel({TICKET_TO_CANCEL}): {retcode} ===")
        if errors:
            print(f"=== Raised (not a retcode -- the call itself failed): "
                  f"{ {t: repr(e) for t, e in errors.items()} } ===")

        all_resolved = (
            not cancel_target_still_present
            and state_store.lookup(TICKET_TO_RECONCILE).status == "CLOSED"
        )
        print(f"\n=== {'PASSED' if all_resolved else 'FAILED'}: both tickets resolved: "
              f"{all_resolved} ===")

    print("\n=== Done. No new order placed. ===")


if __name__ == "__main__":
    asyncio.run(main())
