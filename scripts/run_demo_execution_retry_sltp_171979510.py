#!/usr/bin/env python3
"""
One-off recovery action (not part of Phase 9's scoped deliverables): explicitly-approved retry of
the SL/TP attachment that failed at the end of Step 7 live run #8 (2026-08-11) -- see
docs/PHASE9_FORWARD_TEST_CHECKPOINT.md. `run_demo_execution_pipeline_loop.py` opened a runner
MARKET position (ticket 171979510, SELL 0.01 BTCUSD) via `_submit_market()`, then its mandatory
`modify_position` follow-up failed (retcode=10016, broker message included "current price 0.0"),
leaving the position genuinely OPEN_UNPROTECTED (sl=0.0, tp=0.0) on the real live account. Per
`mcp_order_executor.py`'s own design, `_submit_market()` makes exactly one attempt and never
retries automatically -- recovery requires a separate, explicitly-approved call, made here by a
human decision (user chose "retry SL/TP attach" over "close position" or "monitor").

SAFETY:
- First does a READ-ONLY fresh check: confirms the position is still open, still unprotected, and
  sanity-checks the intended SL/TP against the current live tick before submitting anything --
  aborts with no mutating call if the position has already closed, is no longer unprotected, or if
  the current price has moved through either intended level (which would make the attach either
  meaningless or immediately-triggering).
- Reuses the exact same `modify_position` call, retcode parsing, and live-read verification
  (tolerance-based) that `McpOrderExecutor._submit_market()` itself uses -- not a reinvented path.
- Exactly one `modify_position` attempt, matching the "no retry" doctrine elsewhere in this
  project -- if the broker rejects it again, this script stops and reports rather than looping.
- On confirmed success, calls `StateStore.mark_sl_tp_attached()` (transitions OPEN_UNPROTECTED ->
  OPEN) -- the only local-state write this script makes, and only after live confirmation, exactly
  matching mcp_order_executor.py's own doctrine (never on retcode alone).
- Constructs Settings with mode=DEMO_EXECUTION in code and goes through demo_execution_session()
  like every other script here -- both the reliable env-sourced hard gate and the informational
  MCP-sourced gate are enforced before the mutating call.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.mt5_adapter.metatrader_retcodes import parse_trade_response

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

SYMBOL = "BTCUSD"
TICKET = 171979510
SIDE = "SELL"
INTENDED_SL = 63975.3
INTENDED_TP = 63937.1
_SL_TP_TOLERANCE = 0.5  # matches mcp_order_executor.py's own tolerance


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        # --- read-only pre-check ---
        positions = await account.get_positions(symbol=SYMBOL)
        position = next((p for p in positions if p.ticket == TICKET), None)
        if position is None:
            print(f"ABORT: ticket={TICKET} not found among live positions -- already closed? "
                  f"No mutating call made.")
            return
        print(f"Live position: ticket={position.ticket} side={position.side} "
              f"volume={position.volume} sl={position.sl} tp={position.tp}")
        if not (position.sl == 0.0 and position.tp == 0.0):
            print(f"ABORT: ticket={TICKET} is no longer unprotected (sl={position.sl}, "
                  f"tp={position.tp}) -- someone/something already fixed it. No mutating call made.")
            return

        tick = await McpMarketDataSource(client).get_tick(SYMBOL)
        print(f"Current tick: bid={tick.bid} ask={tick.ask}")
        # SELL: SL must be above current price, TP must be below current price.
        if not (tick.ask < INTENDED_SL and tick.bid > INTENDED_TP):
            print(f"ABORT: current price has moved through an intended level "
                  f"(sl={INTENDED_SL}, tp={INTENDED_TP}, bid={tick.bid}, ask={tick.ask}) -- "
                  f"attaching now would be meaningless or immediately-triggering. "
                  f"No mutating call made -- needs a fresh decision.")
            return

        # --- the one mutating call ---
        print(f"Attempting modify_position(id={TICKET}, stop_loss={INTENDED_SL}, "
              f"take_profit={INTENDED_TP})...")
        raw = await client.call_tool(
            "modify_position", {"id": TICKET, "stop_loss": INTENDED_SL, "take_profit": INTENDED_TP},
        )
        response = parse_trade_response(raw)
        print(f"modify_position response: done={response.done} retcode={response.retcode} "
              f"message={response.tool_message!r}")
        if not response.done:
            print(f"FAILED: modify_position not confirmed done. Ticket {TICKET} remains "
                  f"OPEN_UNPROTECTED. No local state change made. Needs a fresh decision "
                  f"(retry again, or close the position).")
            return

        # --- verify via fresh live read, tolerance-based, exactly like the executor does ---
        positions_after = await account.get_positions(symbol=SYMBOL)
        position_after = next((p for p in positions_after if p.ticket == TICKET), None)
        if position_after is None:
            print(f"FAILED: ticket={TICKET} not found in a fresh read after modify_position. "
                  f"No local state change made.")
            return
        sl_ok = abs(position_after.sl - INTENDED_SL) < _SL_TP_TOLERANCE
        tp_ok = abs(position_after.tp - INTENDED_TP) < _SL_TP_TOLERANCE
        print(f"Fresh live read: sl={position_after.sl} tp={position_after.tp} "
              f"(sl_ok={sl_ok}, tp_ok={tp_ok})")
        if not (sl_ok and tp_ok):
            print(f"FAILED: retcode said done but live sl/tp does not match requested values. "
                  f"Ticket {TICKET} treated as still unprotected. No local state change made.")
            return

        state_store.mark_sl_tp_attached(TICKET)
        print(f"SUCCESS: ticket={TICKET} confirmed protected (sl={position_after.sl}, "
              f"tp={position_after.tp}). Local state transitioned OPEN_UNPROTECTED -> OPEN.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
