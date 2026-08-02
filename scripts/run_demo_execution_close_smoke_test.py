#!/usr/bin/env python3
"""
Phase 6, Step 5: closes a real MT5 position via McpOrderExecutor.close_position().

Live attempt #1 (see docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md, "Step 5") was
correctly refused: the position was opened directly in the MT5 terminal, not through
McpOrderExecutor, so it had no local state record -- reconciliation classified it
`unknown_real`, and ExecutionPosture.MANAGE_ONLY refuses to touch any ticket it can't
attribute. This is the safety design working as intended, not a bug.

This version implements an explicit, narrowly-scoped MANUAL ADOPTION workflow for that one
position, per explicit user instruction. It does NOT fabricate a system-originated record --
see state/models.py's "manual_adoption" origin and StateStore.record_manual_adoption(). It
performs a fresh live verification against exact, hardcoded expected values (symbol, ticket,
side, volume -- see EXPECTED_* below) and adopts ONLY on an exact match on all of them. Any
mismatch aborts before sending any close request. This is a one-off workflow for this specific
ticket, not a general "claim any position" capability.

The expected account server (ThinkMarkets-Demo) is NOT independently machine-verifiable: no
tool on this MCP server exposes login/server (see McpAccountReader's module docstring). This
script relies on the human-stated fact plus the already-established MT5_ACCOUNT_KIND=DEMO
pre-launch gate as this project's real trust boundary for "is this actually the demo account"
-- it does not pretend to verify the server name itself over MCP.

Run only once, with explicit approval. Requirements for this run (per user instruction): demo
account only, exact match required on account/ticket/symbol/side/volume, abort on any mismatch
without closing, one close attempt only, no automatic retry, do not open any new position,
report the exact request/retcode/execution result/MT5 state before and after, keep the audit
record clearly marked manual_adoption (never system_owned).
"""

from __future__ import annotations
import os
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state.json"

# Exact expected values, per explicit user instruction. Adoption proceeds ONLY if every one of
# these matches a fresh live read exactly -- any mismatch aborts before any close request.
EXPECTED_SERVER = "ThinkMarkets-Demo"  # human-stated, not independently verifiable -- see above
EXPECTED_SYMBOL = "BTCUSD"
EXPECTED_TICKET = int(os.environ["EXPECTED_TICKET"])
EXPECTED_SIDE = "BUY"
EXPECTED_VOLUME = 0.01


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Expected account server: {EXPECTED_SERVER!r} (human-stated; the real trust "
          f"boundary here is MT5_ACCOUNT_KIND=DEMO, checked above -- see this script's "
          f"module docstring) ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        positions = await account.get_positions(symbol=EXPECTED_SYMBOL)
        print(f"\n=== Live positions on {EXPECTED_SYMBOL} (before) ===")
        for p in positions:
            print(p)

        matches = [p for p in positions if p.ticket == EXPECTED_TICKET]
        if len(matches) != 1:
            print(f"\nABORT: expected exactly one position with ticket={EXPECTED_TICKET} on "
                  f"{EXPECTED_SYMBOL}, found {len(matches)}. No close request sent.")
            return

        target = matches[0]
        mismatches = []
        if target.symbol != EXPECTED_SYMBOL:
            mismatches.append(f"symbol: expected {EXPECTED_SYMBOL!r}, got {target.symbol!r}")
        if target.side != EXPECTED_SIDE:
            mismatches.append(f"side: expected {EXPECTED_SIDE!r}, got {target.side!r}")
        if target.volume != EXPECTED_VOLUME:
            mismatches.append(f"volume: expected {EXPECTED_VOLUME}, got {target.volume}")

        if mismatches:
            print("\nABORT: live position does not match expected values exactly:")
            for m in mismatches:
                print(f"  - {m}")
            print("No close request sent.")
            return

        print(f"\n=== Exact match confirmed: ticket={target.ticket}, symbol={target.symbol}, "
              f"side={target.side}, volume={target.volume}, price_open={target.price_open}, "
              f"profit={target.profit}, MT5-reported magic={target.magic} (expected 0, per "
              f"the confirmed upstream bug -- see docs/mcp_tool_classification.md item 7) ===")

        adopted_at = datetime.now(timezone.utc)
        note = (
            f"Manually opened by user in the MT5 terminal ({EXPECTED_SERVER}), NOT via "
            f"McpOrderExecutor.submit(). Adopted after an exact live-verified match on "
            f"ticket/symbol/side/volume, per explicit user instruction, for this one Phase 6 "
            f"Step 5 demo smoke test only. This is NOT a system-originated order."
        )
        state_store.record_manual_adoption(
            ticket=target.ticket, symbol=target.symbol, side=target.side,
            volume=target.volume, price_open=target.price_open, magic=target.magic,
            adopted_at=adopted_at, note=note,
        )
        adopted_record = state_store.lookup(target.ticket)
        print(f"\n=== Recorded local state (origin={adopted_record.origin!r}) ===\n"
              f"{adopted_record}")

        print(f"\n=== Exact request: close_position(ticket={target.ticket}, "
              f"volume={target.volume}) ===")
        result = await executor.close_position(target.ticket, volume=target.volume)
        print(f"\n=== ExecutionResult ===\n{result}")

        state_after = state_store.lookup(target.ticket)
        print(f"\n=== local state after close (origin={state_after.origin!r}) ===\n"
              f"{state_after}")

        positions_after = await account.get_positions(symbol=EXPECTED_SYMBOL)
        print(f"\n=== Live positions on {EXPECTED_SYMBOL} (after): {len(positions_after)} ===")
        for p in positions_after:
            print(p)

    print("\n=== Done. Single attempt, no retry. No new position was opened. ===")


if __name__ == "__main__":
    asyncio.run(main())
