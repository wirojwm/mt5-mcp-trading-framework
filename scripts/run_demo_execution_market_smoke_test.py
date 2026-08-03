#!/usr/bin/env python3
"""
Phase 6, Step 6: places a single real MT5 MARKET order with mandatory SL/TP via
McpOrderExecutor.submit(), then closes the resulting position -- the smallest possible real
action that proves the whole MARKET path (place_market_order -> confirmed-done retcode ->
local state written OPEN_UNPROTECTED -> mandatory modify_position attach -> live-verified
SL/TP -> state transitioned to OPEN) actually works end-to-end, not just against mocks.

See docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md, "Step 6" for the full design this
mirrors, and mt5_adapter/mcp_order_executor.py's module docstring for _submit_market()'s exact
behavior. Run only once, with explicit approval, reviewed live -- do not add this to any
automated/scheduled run.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, overriding whatever
  MT5_MCP_MODE is set to in .env -- same reasoning as run_demo_execution_smoke_test.py.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Aborts before submitting anything if a position already exists for SMOKE_TEST_MAGIC on
  SYMBOL -- a leftover from a previous incomplete run (e.g. an OPEN_UNPROTECTED ticket from an
  attach failure that was never separately remediated) must be dealt with on its own, not
  silently added to.
- Volume is the symbol's live volume_min (the smallest size the broker allows).
- SL/TP are computed live from the current tick and the symbol's stops_level/freeze_level (the
  same gap formula order_planning/limit_price.py already uses for LIMIT prices), with a wide
  safety multiplier on top -- chosen so the mandatory modify_position attach is unlikely to be
  rejected by the broker's own minimum-distance check, since McpOrderExecutor deliberately does
  not pre-validate that distance itself (see mcp_order_executor.py's module docstring for why).
- Exactly ONE submit() call. McpOrderExecutor.submit() internally makes exactly one
  place_market_order call and, only if that succeeds, exactly one modify_position call -- no
  retry of either, ever, regardless of outcome (this script does not add any retry on top).
- If SlTpAttachmentFailedError is raised, THIS SCRIPT DOES NOT ATTEMPT ANY CLEANUP. Per the
  approved Phase 6 Step 6 design, a failed attach requires its own separate, explicitly-approved
  recovery action -- auto-closing here would itself be a second unattended live trading
  decision, exactly what that design refuses to do. The script reports the ticket and stops.
- Only on full success (position opened AND SL/TP confirmed attached) does this script proceed
  to its own designed cleanup: one close_position() call, mirroring how Step 4 always cancels
  its own successfully-placed test LIMIT order. This is the intentional "prove it round-trips,
  then leave the account clean" pattern already established for every prior live step -- not an
  auto-remediation of a failure.
- Never reads, logs, or prints .env or any credential.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.domain.models import OrderPlan
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.mt5_adapter.mcp_order_executor import SlTpAttachmentFailedError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state.json"

SYMBOL = "BTCUSD"
SMOKE_TEST_MAGIC = 79999  # same convention as Step 4/5's smoke tests -- deliberately outside
# the 71101/72101 grid/runner range, and not in strategy_registry.py's known map, so it always
# resolves to a loud "unknown_magic_79999" rather than being mistaken for a real strategy.

# Safety multiplier on top of (stops_level + freeze_level + 2) * point -- the same minimum-gap
# formula order_planning/limit_price.py uses for LIMIT prices, reused here as a sane distance
# for SL/TP even though McpOrderExecutor itself does not enforce it (see this script's
# docstring). Chosen generously since this account's SL/TP-rejection behavior at the boundary
# has never been observed live -- better to clear it comfortably than to spend this run's one
# attempt discovering the exact edge.
GAP_SAFETY_MULTIPLIER = 10


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        positions_before = await account.get_positions(symbol=SYMBOL, magic=SMOKE_TEST_MAGIC)
        print(f"\n=== Live positions on {SYMBOL} with magic={SMOKE_TEST_MAGIC} (before): "
              f"{len(positions_before)} ===")
        for p in positions_before:
            print(p)
        if positions_before:
            print(f"\nABORT: {len(positions_before)} position(s) already exist for "
                  f"magic={SMOKE_TEST_MAGIC} -- likely a leftover from a previous incomplete "
                  f"run. Resolve that separately before running this script again. No order "
                  f"submitted.")
            return

        market_data = McpMarketDataSource(client)
        tick = await market_data.get_tick(SYMBOL)
        symbol_info = await market_data.get_symbol_info(SYMBOL)
        gap = (symbol_info.stops_level + symbol_info.freeze_level + 2) * symbol_info.point
        offset = round(gap * GAP_SAFETY_MULTIPLIER, symbol_info.digits)
        reference_price = tick.ask  # a BUY market order fills at or near the current ask
        sl = round(reference_price - offset, symbol_info.digits)
        tp = round(reference_price + offset, symbol_info.digits)
        volume = symbol_info.volume_min

        print(f"\n=== Live tick: bid={tick.bid}, ask={tick.ask} ===")
        print(f"=== symbol_info: digits={symbol_info.digits}, point={symbol_info.point}, "
              f"stops_level={symbol_info.stops_level}, freeze_level={symbol_info.freeze_level}, "
              f"volume_min={volume} ===")
        print(f"=== Computed: reference_price={reference_price}, gap={gap}, offset={offset} "
              f"({GAP_SAFETY_MULTIPLIER}x gap), sl={sl}, tp={tp}, volume={volume} ===")

        order_plan = OrderPlan(
            symbol=SYMBOL, order_type="MARKET", side="BUY", volume=volume, price=reference_price,
            sl=sl, tp=tp, deviation=150, magic=SMOKE_TEST_MAGIC,
            comment="phase6_step6_market_smoke_test",
        )

        print(f"\n=== submit({order_plan}) -- ONE attempt, no retry ===")
        try:
            result = await executor.submit(order_plan)
        except SlTpAttachmentFailedError as exc:
            print(f"\n=== SL/TP ATTACHMENT FAILED: ticket={exc.ticket}, "
                  f"reason={exc.reason!r}, retcode={exc.retcode} ===")
            print("Position is OPEN and UNPROTECTED on the account. Local state already "
                  f"reflects status=OPEN_UNPROTECTED for ticket={exc.ticket} (see "
                  f"state_store.lookup({exc.ticket}) if inspecting after this run).")
            print("Per the approved Phase 6 Step 6 design, NO automatic retry or close is "
                  "attempted by this script. Recovery requires its own separate, explicitly "
                  "approved action -- do not re-run this script until that ticket is resolved.")
            return

        print(f"\n=== ExecutionResult ===\n{result}")

        if not result.success:
            print("\n=== Order was rejected (retcode not TRADE_RETCODE_DONE) -- nothing was "
                  "opened, nothing to clean up. ===")
            return

        assert result.ticket is not None  # success=True from _submit_market always carries one
        print(f"\n=== PASSED: MARKET order opened (ticket={result.ticket}) and SL/TP confirmed "
              f"attached against real MT5 state (verified={result.verified}) ===")

        # This script's own designed cleanup -- reached ONLY on full success, mirroring Step
        # 4's cancel-in-finally / Step 5's close pattern, never a response to failure.
        print(f"\n=== Cleanup: closing ticket={result.ticket} (one attempt, no retry) ===")
        print(f"state before close: {state_store.lookup(result.ticket)}")

        close_result = await executor.close_position(result.ticket)
        print(f"\n=== ExecutionResult (close) ===\n{close_result}")

        print(f"state after close: {state_store.lookup(result.ticket)}")

        positions_after = await account.get_positions(symbol=SYMBOL, magic=SMOKE_TEST_MAGIC)
        print(f"\n=== Live positions on {SYMBOL} with magic={SMOKE_TEST_MAGIC} (after): "
              f"{len(positions_after)} ===")
        for p in positions_after:
            print(p)

    print("\n=== Done. Single attempt, no retry. No position left open. ===")


if __name__ == "__main__":
    asyncio.run(main())
