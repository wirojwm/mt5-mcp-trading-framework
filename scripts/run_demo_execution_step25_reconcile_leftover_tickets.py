#!/usr/bin/env python3
"""
Reconciles the 6 tickets Step 25 (docs/PIPELINE_WIRING_CHECKPOINT.md) left open on the demo
account when the loop was stopped safely mid-run. A follow-up read-only check
(scripts/run_demo_execution_step25_ticket_status_check.py) found all 6 already ABSENT from live
positions AND live pending orders -- most likely closed/filled-then-closed via their own
broker-side SL/TP, the same pattern observed repeatedly throughout this project (Steps 9, 15, 17,
20). Local StateStore still says OPEN (stale) for all 6, since no close_position()/cancel() call
was ever made for any of them by this project.

TICKETS = (171649460, 171649631, 171648990, 171649324, 171649422, 171649461)

One-off, narrowly-scoped script: acts on ONLY these six hardcoded tickets, nothing else, ever.
Pure local-state reconciliation -- no MCP order call of any kind, since nothing remains live on
the broker side for any of them.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Re-verifies live state for all 6 tickets immediately before acting (not trusting the earlier
  report -- state is known to move between a report and any follow-up action): each ticket must
  be ABSENT from both live positions and live pending orders. Aborts, with no action attempted on
  ANY ticket, if even one ticket turns out to still be live.
- Each ticket gets exactly one direct StateStore.record_closed() call, no MCP call at all.
- Places, modifies, and cancels no order of any kind.
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
TICKETS = (171649460, 171649631, 171648990, 171649324, 171649422, 171649461)


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Reconciling {len(TICKETS)} tickets via StateStore.record_closed() only -- "
          f"no MCP call, no retry, nothing new placed ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses ===")

        positions_before = await account.get_positions(symbol=SYMBOL)
        orders_before = await account.get_orders(symbol=SYMBOL)  # unfiltered by magic
        live_position_tickets = {p.ticket for p in positions_before}
        live_order_tickets = {o.ticket for o in orders_before}

        print(f"\n=== BEFORE: live positions on {SYMBOL} (all magics): {len(positions_before)} ===")
        for p in positions_before:
            print(f"  {p}")
        print(f"=== BEFORE: live pending orders on {SYMBOL} (all magics): {len(orders_before)} ===")
        for o in orders_before:
            print(f"  {o}")

        still_live = [
            t for t in TICKETS if t in live_position_tickets or t in live_order_tickets
        ]
        for t in TICKETS:
            print(f"  {t}: absent={t not in live_position_tickets and t not in live_order_tickets}"
                  f"  | local: {state_store.lookup(t)}")

        if still_live:
            print(f"\nABORT: {still_live} still live -- refusing to proceed on an inconsistent "
                  f"premise. No action attempted on ANY ticket.")
            return

        print(f"\n=== All {len(TICKETS)} tickets confirmed absent -- reconciling local state only ===")
        errors = {}
        for t in TICKETS:
            try:
                state_store.record_closed(
                    t,
                    reason="confirmed absent from live positions/orders -- closed outside this "
                           "process (no close_position()/cancel() call was ever made for it), "
                           "most likely filled then closed via its own broker-side SL/TP, or "
                           "expired/cancelled elsewhere",
                    closed_at=datetime.now(timezone.utc),
                )
                print(f"  {t}: {state_store.lookup(t)}")
            except Exception as exc:
                print(f"  record_closed({t}) raised: {exc!r}")
                errors[t] = exc

        all_resolved = all(
            state_store.lookup(t) is not None and state_store.lookup(t).status == "CLOSED"
            for t in TICKETS
        )
        print(f"\n=== {'PASSED' if all_resolved and not errors else 'FAILED'}: "
              f"all {len(TICKETS)} tickets reconciled to CLOSED: {all_resolved and not errors} ===")
        if errors:
            print(f"=== Raised: { {t: repr(e) for t, e in errors.items()} } ===")

    print("\n=== Done. No MCP order call made, no new order placed. ===")


if __name__ == "__main__":
    asyncio.run(main())
