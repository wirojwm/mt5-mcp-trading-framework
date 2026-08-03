#!/usr/bin/env python3
"""
One-off cleanup for the first (stopped) bounded-autonomous-loop run: resolves every ticket that
run created, scoped STRICTLY to those tickets -- does not touch the 3 pre-existing tickets
(171622543/171622789/171622791) left open by earlier, separate, deliberate decisions.

Live state moved twice between diagnosis and this cleanup being written (confirmed via repeated
independent read-only checks): of the 6 tickets this loop run created across its 2 cycles,
4 had already closed on their own via their own SL/TP before this script was even written --
171623173 (grid BUY_LIMIT), 171623174 (grid SELL_LIMIT, had filled then closed),
171623175 (runner BUY MARKET, opened then closed), 171623293 (grid SELL_LIMIT). Only 2 were
still genuinely live: 171623291 (grid BUY_LIMIT, still pending) and 171623294 (runner BUY
MARKET, still an open position).

This script re-verifies EVERY ticket's live status immediately before acting on it (not trusting
the snapshot above, which is itself already a moment in the past) -- for each of the 6:
- if present as a live pending order -> cancel() (one attempt, no retry)
- if present as a live position -> close_position() (one attempt, no retry)
- if absent from both -> reconcile locally only (StateStore.record_closed(), no MCP call --
  nothing left on the broker side to act on), mirroring the established Step 9 precedent

One-off, narrowly-scoped script: acts on ONLY these 6 hardcoded tickets, nothing else, ever.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Places no new order of any kind -- cancel/close/reconcile only.
- Each ticket handled independently in try/except -- one ticket's failure never blocks another.
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
TICKETS = (171623173, 171623174, 171623175, 171623291, 171623293, 171623294)  # this run's tickets, only these


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Resolving ONLY tickets={TICKETS} (this loop run's tickets) -- "
          f"re-verifying each live immediately before acting, no new order ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        positions = await account.get_positions(symbol=SYMBOL)
        orders = await account.get_orders(symbol=SYMBOL)
        positions_by_ticket = {p.ticket: p for p in positions}
        orders_by_ticket = {o.ticket: o for o in orders}

        print(f"\n=== Live positions on {SYMBOL} (all magics): {len(positions)} ===")
        for p in positions:
            print(f"  {p}")
        print(f"=== Live pending orders on {SYMBOL} (all magics): {len(orders)} ===")
        for o in orders:
            print(f"  {o}")

        results = {}
        errors = {}

        for ticket in TICKETS:
            if ticket in positions_by_ticket:
                print(f"\n=== {ticket}: live position -> close_position() (one attempt) ===")
                try:
                    results[ticket] = await executor.close_position(ticket)
                    print(f"  {results[ticket]}")
                except Exception as exc:
                    print(f"  close_position({ticket}) raised: {exc!r}")
                    errors[ticket] = exc
            elif ticket in orders_by_ticket:
                print(f"\n=== {ticket}: live pending order -> cancel() (one attempt) ===")
                try:
                    results[ticket] = await executor.cancel(ticket)
                    print(f"  {results[ticket]}")
                except Exception as exc:
                    print(f"  cancel({ticket}) raised: {exc!r}")
                    errors[ticket] = exc
            else:
                print(f"\n=== {ticket}: absent from both live positions and orders -> "
                      f"reconcile locally only (no MCP call) ===")
                try:
                    state_store.record_closed(
                        ticket,
                        reason="confirmed absent from live positions/orders during loop-run "
                               "cleanup -- closed outside this cleanup action (most likely via "
                               "its own broker-side SL/TP execution)",
                        closed_at=datetime.now(timezone.utc),
                    )
                    print(f"  state_store.lookup({ticket}) = {state_store.lookup(ticket)}")
                except Exception as exc:
                    print(f"  record_closed({ticket}) raised: {exc!r}")
                    errors[ticket] = exc

        positions_after = await account.get_positions(symbol=SYMBOL)
        orders_after = await account.get_orders(symbol=SYMBOL)
        print(f"\n=== AFTER: live positions on {SYMBOL} (all magics): {len(positions_after)} ===")
        for p in positions_after:
            print(f"  {p}")
        print(f"=== AFTER: live pending orders on {SYMBOL} (all magics): {len(orders_after)} ===")
        for o in orders_after:
            print(f"  {o}")
        print(f"\n=== Final local state for this run's tickets: ===")
        for ticket in TICKETS:
            print(f"  {ticket}: {state_store.lookup(ticket)}")

        remaining = {t for t in TICKETS if t in {p.ticket for p in positions_after}
                     or t in {o.ticket for o in orders_after}}
        print(f"\n=== {'FAILED' if remaining else 'PASSED'}: tickets still live after cleanup: "
              f"{remaining or 'none'} ===")
        if errors:
            print(f"=== Raised (not a retcode -- the call itself failed): "
                  f"{ {t: repr(e) for t, e in errors.items()} } ===")

    print("\n=== Done. No new order placed. Pre-existing tickets "
          "(171622543/171622789/171622791) intentionally untouched -- out of scope. ===")


if __name__ == "__main__":
    asyncio.run(main())
