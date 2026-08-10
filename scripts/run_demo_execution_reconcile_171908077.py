#!/usr/bin/env python3
"""
Reconciles ticket 171908077 (grid SELL_LIMIT, magic=71101) to CLOSED. Root-caused directly, not
guessed: a prior read-only investigation (scripts/run_demo_execution_investigate_ticket_171909600.py)
found real deal 99944397 (order=171909600, position_id=171908077, entry=OUT, price=65093.81,
profit=-0.6, comment="[sl 65087.71]") -- MT5's own auto-generated stop-loss execution, matching this
ticket's local requested_sl=65087.71 exactly. Local StateStore still shows it as OPEN (stale, the
same "no automatic broker-side-close reconciliation" pattern this project has already documented
repeatedly), since no close_position() call was ever made for it by this project.

One-off, narrowly-scoped script: acts on ONLY this one hardcoded ticket, nothing else, ever. Pure
local-state reconciliation -- no MCP order call of any kind, since nothing remains live on the
broker side for it.

SAFETY (same discipline as every prior single-ticket reconcile script in this project, e.g.
run_demo_execution_reconcile_171651879.py):
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Re-verifies TICKET is absent from both live positions and live pending orders immediately
  before acting (not trusting the earlier investigation alone); aborts with no action if it turns
  out to still be live.
- Exactly one direct StateStore.record_closed() call, no MCP order call at all.
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
TICKET = 171908077  # grid SELL_LIMIT, magic=71101 -- confirmed closed via real SL deal 99944397


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Reconciling {TICKET} via StateStore.record_closed() only -- no MCP order call ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del client, executor  # deliberately unused -- read-only re-verify + local state write only
        print("=== Connected via the same wrapper Claude Code uses ===")

        positions_before = await account.get_positions(symbol=SYMBOL)  # unfiltered by magic
        orders_before = await account.get_orders(symbol=SYMBOL)  # unfiltered by magic
        target_absent = (
            not any(p.ticket == TICKET for p in positions_before)
            and not any(o.ticket == TICKET for o in orders_before)
        )

        print(f"\n=== BEFORE: live positions on {SYMBOL} (all magics): {len(positions_before)} ===")
        for p in positions_before:
            print(f"  {p}")
        print(f"=== BEFORE: live pending orders on {SYMBOL} (all magics): {len(orders_before)} ===")
        for o in orders_before:
            print(f"  {o}")
        print(f"=== target ({TICKET}) absent from live positions/orders: {target_absent} ===")
        print(f"  state_store.lookup({TICKET}) = {state_store.lookup(TICKET)}")

        if not target_absent:
            print(f"\nABORT: ticket {TICKET} is still live -- refusing to proceed on an "
                  f"inconsistent premise. No action attempted.")
            return

        print(f"\n=== Reconciling {TICKET} -- local StateStore only, no MCP call "
              f"(nothing left on the broker side to act on) ===")
        try:
            state_store.record_closed(
                TICKET,
                reason="confirmed closed via real deal 99944397 (order=171909600, "
                       "position_id=171908077, entry=OUT, price=65093.81, profit=-0.6, "
                       "comment='[sl 65087.71]') -- MT5's own auto-generated stop-loss execution "
                       "matching this ticket's requested_sl=65087.71 exactly, root-caused by "
                       "run_demo_execution_investigate_ticket_171909600.py; no close_position() "
                       "call was ever made for it by this project",
                closed_at=datetime.now(timezone.utc),
            )
            print(f"state_store.lookup({TICKET}) = {state_store.lookup(TICKET)}")
        except Exception as exc:
            print(f"record_closed({TICKET}) raised: {exc!r}")

        rec = state_store.lookup(TICKET)
        passed = rec is not None and rec.status == "CLOSED"
        print(f"\n=== {'PASSED' if passed else 'FAILED'}: ticket {TICKET} reconciled to CLOSED: {passed} ===")

    print("\n=== Done. No MCP order call made, no new order placed. ===")


if __name__ == "__main__":
    asyncio.run(main())
