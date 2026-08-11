#!/usr/bin/env python3
"""
One-off recovery action (not part of Phase 9's scoped deliverables): explicitly-approved close of
ticket 171979510 -- see docs/PHASE9_FORWARD_TEST_CHECKPOINT.md. Step 7 live run #8 (2026-08-11)
opened this runner MARKET position (SELL 0.01 BTCUSD) but its mandatory SL/TP attach failed
(retcode=10016), leaving it OPEN_UNPROTECTED. A read-only retry-sanity-check
(run_demo_execution_retry_sltp_171979510.py) then found the original intended SL/TP levels were
stale -- price had moved through them in the ~2 hours since open -- so no blind retry was
attempted. User was given live P&L (-$0.95 at the time) and chose to close the position outright
rather than compute new SL/TP levels or keep monitoring it unprotected.

Uses McpOrderExecutor.close_position() directly -- the same already-proven path every other
close in this project goes through (gates, reconciliation posture check, the close call, and
StateStore.record_closed() on confirmation) -- not a reinvented one-off call.
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
TICKET = 171979510


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del client, state_store  # executor owns both internally

        positions = await account.get_positions(symbol=SYMBOL)
        position = next((p for p in positions if p.ticket == TICKET), None)
        if position is None:
            print(f"ABORT: ticket={TICKET} not found among live positions -- already closed? "
                  f"No mutating call made.")
            return
        print(f"Live position before close: ticket={position.ticket} side={position.side} "
              f"volume={position.volume} sl={position.sl} tp={position.tp} "
              f"profit={position.profit}")

        result = await executor.close_position(TICKET)
        print(f"\nExecutionResult: success={result.success} retcode={result.retcode} "
              f"deal={result.deal} executed_price={result.executed_price} verified={result.verified}")
        print(f"broker_comment={result.broker_comment!r}")
        print(f"verification_details={result.verification_details!r}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
