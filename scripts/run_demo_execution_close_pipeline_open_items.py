#!/usr/bin/env python3
"""
Pipeline-wiring cleanup: resolves the 3 items left intentionally open on the demo account by
the pipeline-wiring live runs (docs/PIPELINE_WIRING_CHECKPOINT.md, Steps 7-8) --

- ticket 171621825: SELL MARKET position, magic=72101, strategy="runner" -- confirmed ALREADY
  ABSENT from the live account (checked live, right before this script was written): most likely
  closed automatically by the broker hitting its own SL (62572.03) or TP (62519.44), since this
  ticket's local record was never touched by any close_position()/cancel() call this project
  made. Local state (StateStore) has no way to learn about a broker-side closure on its own --
  it only updates when McpOrderExecutor explicitly calls record_closed()/record_cancelled() --
  so this ticket's local record is stale (still says OPEN) even though the real position is
  gone. This script reconciles that: re-verifies absence live, then calls
  StateStore.record_closed() directly (no MCP call involved -- there's nothing left to
  close/cancel on the broker side for a ticket that's already gone).
- ticket 171621926: was a BUY_LIMIT pending order (grid, magic=71101) as of the last report, but
  has since FILLED into a real, live BUY POSITION (confirmed live, right before this script was
  written) -- closed via McpOrderExecutor.close_position(), not cancel().
- ticket 171621927: SELL_LIMIT pending order, magic=71101, strategy="grid" -- unchanged, still
  live -- cancelled via McpOrderExecutor.cancel().

One-off, narrowly-scoped script: acts on ONLY these three hardcoded tickets, nothing else,
ever -- not a general-purpose cancel/close/reconcile tool. Run only once, with explicit
approval, reviewed live.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Re-verifies live state for all three tickets immediately before acting (not trusting the
  earlier report, which is exactly what caught the account having moved on the previous run of
  this script): TICKET_TO_CLOSE must be a live position, TICKET_TO_CANCEL must be a live pending
  order, TICKET_TO_RECONCILE must be ABSENT from live positions. Aborts, with no action attempted
  on ANY ticket, if any of these three conditions doesn't hold.
- Exactly ONE attempt per ticket (one close_position() call, one cancel() call, one direct
  StateStore.record_closed() call for the reconciliation-only ticket) -- no retry of any,
  regardless of outcome. Each is wrapped independently in try/except so one ticket's failure
  never blocks the others from being attempted.
- Verifies the close/cancel targets are ABSENT afterward (from get_positions()/get_orders()
  respectively).
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state"  # directory: one <ticket>.json file per ticket

SYMBOL = "BTCUSD"
TICKET_TO_CLOSE = 171621926     # now a live BUY position (grid's BUY_LIMIT filled)
TICKET_TO_CANCEL = 171621927    # still a live SELL_LIMIT pending order (grid)
TICKET_TO_RECONCILE = 171621825  # already absent live (runner) -- local state only, no MCP call


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Closing position {TICKET_TO_CLOSE}, cancelling order {TICKET_TO_CANCEL}, "
          f"reconciling already-absent {TICKET_TO_RECONCILE} -- one attempt each, no retry, "
          f"no new order ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        positions_before = await account.get_positions(symbol=SYMBOL)
        orders_before = await account.get_orders(symbol=SYMBOL)  # unfiltered by magic -- see SAFETY
        close_target_present = any(p.ticket == TICKET_TO_CLOSE for p in positions_before)
        cancel_target_present = any(o.ticket == TICKET_TO_CANCEL for o in orders_before)
        reconcile_target_absent = not any(p.ticket == TICKET_TO_RECONCILE for p in positions_before)

        print(f"\n=== BEFORE: live positions on {SYMBOL} (all magics): {len(positions_before)} ===")
        for p in positions_before:
            print(f"  {p}")
        print(f"=== BEFORE: live pending orders on {SYMBOL} (all magics): {len(orders_before)} ===")
        for o in orders_before:
            print(f"  {o}")
        print(f"=== close target ({TICKET_TO_CLOSE}) present as a position: {close_target_present} ===")
        print(f"=== cancel target ({TICKET_TO_CANCEL}) present as a pending order: "
              f"{cancel_target_present} ===")
        print(f"=== reconcile target ({TICKET_TO_RECONCILE}) absent from live positions: "
              f"{reconcile_target_absent} ===")
        for ticket in (TICKET_TO_CLOSE, TICKET_TO_CANCEL, TICKET_TO_RECONCILE):
            print(f"  state_store.lookup({ticket}) = {state_store.lookup(ticket)}")

        if not (close_target_present and cancel_target_present and reconcile_target_absent):
            print(f"\nABORT: live state doesn't match the premise this script was written "
                  f"against -- refusing to proceed on an inconsistent premise. No action "
                  f"attempted on ANY ticket.")
            return

        results = {}
        errors = {}

        print(f"\n=== close_position({TICKET_TO_CLOSE}) -- ONE attempt, no retry ===")
        try:
            results[TICKET_TO_CLOSE] = await executor.close_position(TICKET_TO_CLOSE)
            print(results[TICKET_TO_CLOSE])
        except Exception as exc:
            print(f"close_position({TICKET_TO_CLOSE}) raised: {exc!r}")
            errors[TICKET_TO_CLOSE] = exc

        print(f"\n=== cancel({TICKET_TO_CANCEL}) -- ONE attempt, no retry ===")
        try:
            results[TICKET_TO_CANCEL] = await executor.cancel(TICKET_TO_CANCEL)
            print(results[TICKET_TO_CANCEL])
        except Exception as exc:
            print(f"cancel({TICKET_TO_CANCEL}) raised: {exc!r}")
            errors[TICKET_TO_CANCEL] = exc

        print(f"\n=== Reconciling {TICKET_TO_RECONCILE} -- local StateStore only, no MCP call "
              f"(nothing left on the broker side to act on) ===")
        try:
            state_store.record_closed(
                TICKET_TO_RECONCILE,
                reason="confirmed absent from live positions -- closed outside this process "
                       "(no close_position() call was ever made for it by this project's "
                       "scripts), most likely via broker-side SL/TP execution",
                closed_at=datetime.now(timezone.utc),
            )
            print(f"state_store.lookup({TICKET_TO_RECONCILE}) = "
                  f"{state_store.lookup(TICKET_TO_RECONCILE)}")
        except Exception as exc:
            print(f"record_closed({TICKET_TO_RECONCILE}) raised: {exc!r}")
            errors[TICKET_TO_RECONCILE] = exc

        positions_after = await account.get_positions(symbol=SYMBOL)
        orders_after = await account.get_orders(symbol=SYMBOL)
        close_target_still_present = any(p.ticket == TICKET_TO_CLOSE for p in positions_after)
        cancel_target_still_present = any(o.ticket == TICKET_TO_CANCEL for o in orders_after)

        print(f"\n=== AFTER: live positions on {SYMBOL} (all magics): {len(positions_after)} ===")
        for p in positions_after:
            print(f"  {p}")
        print(f"=== AFTER: live pending orders on {SYMBOL} (all magics): {len(orders_after)} ===")
        for o in orders_after:
            print(f"  {o}")
        print(f"=== close target present after: {close_target_still_present} ===")
        print(f"=== cancel target present after: {cancel_target_still_present} ===")
        for ticket in (TICKET_TO_CLOSE, TICKET_TO_CANCEL, TICKET_TO_RECONCILE):
            print(f"  state_store.lookup({ticket}) = {state_store.lookup(ticket)}")

        retcodes = {
            TICKET_TO_CLOSE: results[TICKET_TO_CLOSE].retcode if TICKET_TO_CLOSE in results else None,
            TICKET_TO_CANCEL: results[TICKET_TO_CANCEL].retcode if TICKET_TO_CANCEL in results else None,
        }
        print(f"\n=== Retcodes: {retcodes} ===")
        if errors:
            print(f"=== Raised (not a retcode -- the call itself failed): "
                  f"{ {t: repr(e) for t, e in errors.items()} } ===")

        all_resolved = (
            not close_target_still_present and not cancel_target_still_present
            and state_store.lookup(TICKET_TO_RECONCILE).status == "CLOSED"
        )
        print(f"\n=== {'PASSED' if all_resolved else 'FAILED'}: all three items resolved: "
              f"{all_resolved} ===")

    print("\n=== Done. No new order placed. ===")


if __name__ == "__main__":
    asyncio.run(main())
