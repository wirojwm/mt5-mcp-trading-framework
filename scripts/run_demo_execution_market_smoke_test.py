#!/usr/bin/env python3
"""
Phase 6, Steps 6-7: places a single real MT5 MARKET order with mandatory SL/TP via
McpOrderExecutor.submit(), then closes the resulting position -- the smallest possible real
action that proves the whole MARKET path (place_market_order -> confirmed-done retcode ->
local state written OPEN_UNPROTECTED -> mandatory modify_position attach -> live-verified
SL/TP -> state transitioned to OPEN) actually works end-to-end, not just against mocks.

Step 6 (2026-08-03) live-proved this for SIDE="BUY" only (both the attach-failure and
attach-success paths -- see the checkpoint doc's "Step 6" entries). Step 7 generalizes SIDE to
also cover "SELL" -- _validate_market_sl_tp()'s SELL branch and the full SELL flow through
_submit_market() had mocked coverage added (test_mt5_adapter_mcp_order_executor.py) but were
never live-proven before Step 7. Flip SIDE below to choose which direction this run exercises.

See docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md, "Step 6"/"Step 7" for the full design
this mirrors, and mt5_adapter/mcp_order_executor.py's module docstring for _submit_market()'s
exact behavior. Run only once, with explicit approval, reviewed live -- do not add this to any
automated/scheduled run.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, overriding whatever
  MT5_MCP_MODE is set to in .env -- same reasoning as run_demo_execution_smoke_test.py.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Aborts before submitting anything if a LOCAL state record for SMOKE_TEST_MAGIC is still
  OPEN/OPEN_UNPROTECTED -- a leftover from a previous incomplete run (e.g. an OPEN_UNPROTECTED
  ticket from an attach failure that was never separately remediated) must be dealt with on its
  own, not silently added to. Checked against StateStore, NOT a live-position magic filter --
  confirmed live (2026-08-03, ticket 171617865) that MT5 always reports magic=0 on positions
  this project places, so a live filter by magic can never actually detect a leftover; local
  state is the only reliable source for "did this script open something previously."
- Volume is the symbol's live volume_min (the smallest size the broker allows).
- SL/TP are computed live from the current tick and the symbol's stops_level/freeze_level (the
  same gap formula order_planning/limit_price.py already uses for LIMIT prices), with a wide
  safety margin on top -- chosen so the mandatory modify_position attach is unlikely to be
  rejected by the broker's own minimum-distance check, since McpOrderExecutor deliberately does
  not pre-validate that distance itself (see mcp_order_executor.py's module docstring for why).
  The margin is the LARGER of a gap-multiplier and a percentage of price, not gap-multiplier
  alone: confirmed live (2026-08-03) that a 10x-gap offset ($1.20 on a ~$62,880 BTCUSD price,
  ~0.002%) was still rejected with retcode 10016 ("Invalid stops") -- a points-based gap alone
  scales badly for a high-priced instrument, so a price-percentage floor now dominates for
  symbols like this one.
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state"  # directory: one <ticket>.json file per ticket

SYMBOL = "BTCUSD"
SIDE = "SELL"  # Phase 6 Step 7: MARKET SELL-side live test. Step 6 already live-proved BUY
# (tickets 171617865, 171618036) -- this run exercises the SELL branch of
# _validate_market_sl_tp() and the full SELL flow through _submit_market() live for the first
# time. Flip back to "BUY" to re-exercise that side instead; both are equally supported.
SMOKE_TEST_MAGIC = 79999  # same convention as Step 4/5's smoke tests -- deliberately outside
# the 71101/72101 grid/runner range, and not in strategy_registry.py's known map, so it always
# resolves to a loud "unknown_magic_79999" rather than being mistaken for a real strategy.

# Safety multiplier on top of (stops_level + freeze_level + 2) * point -- the same minimum-gap
# formula order_planning/limit_price.py uses for LIMIT prices. Kept as a floor for low-priced
# instruments where this term could dominate, but proven live (2026-08-03, BTCUSD) to be far
# too small on its own for a high-priced instrument -- see MIN_SL_TP_FRACTION_OF_PRICE below,
# which is what actually governed the fix.
GAP_SAFETY_MULTIPLIER = 10

# Floor as a fraction of the reference price -- the dominant term for BTCUSD and any other
# high-priced instrument, where a points-based stops_level gap is negligible relative to price
# (confirmed live: a 10x-gap offset of $1.20 on ~$62,880, ~0.002% of price, was rejected with
# retcode 10016 "Invalid stops"). 1% is deliberately generous: this is a smoke test that closes
# itself immediately on success, not a real strategy needing a tight stop, and the account only
# gets one attempt -- better to clear the broker's real (unknown, unpublished) minimum
# comfortably than to spend that attempt re-discovering the edge.
MIN_SL_TP_FRACTION_OF_PRICE = 0.01


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        # Live positions on SYMBOL, for visibility/audit only -- NOT the abort gate. MT5
        # always reports magic=0 on positions this project places (confirmed live,
        # 2026-08-03), so a live-position magic filter could never detect a leftover.
        positions_before = await account.get_positions(symbol=SYMBOL)
        print(f"\n=== Live positions on {SYMBOL} (before, all magics): {len(positions_before)} ===")
        for p in positions_before:
            print(p)

        # The actual abort gate: local state is the only reliable record of what THIS script
        # opened previously, since MT5's own magic field can't be used to tell.
        leftover_records = [r for r in state_store.all_open() if r.magic == SMOKE_TEST_MAGIC]
        print(f"\n=== Local state records for magic={SMOKE_TEST_MAGIC} still open: "
              f"{len(leftover_records)} ===")
        for r in leftover_records:
            print(r)
        if leftover_records:
            print(f"\nABORT: {len(leftover_records)} local record(s) for "
                  f"magic={SMOKE_TEST_MAGIC} are still OPEN/OPEN_UNPROTECTED -- likely a "
                  f"leftover from a previous incomplete run. Resolve that separately before "
                  f"running this script again. No order submitted.")
            return

        market_data = McpMarketDataSource(client)
        tick = await market_data.get_tick(SYMBOL)
        symbol_info = await market_data.get_symbol_info(SYMBOL)
        # A BUY market order fills at or near the current ask; SELL at or near the current bid.
        reference_price = tick.ask if SIDE == "BUY" else tick.bid
        gap = (symbol_info.stops_level + symbol_info.freeze_level + 2) * symbol_info.point
        gap_offset = gap * GAP_SAFETY_MULTIPLIER
        price_fraction_offset = reference_price * MIN_SL_TP_FRACTION_OF_PRICE
        offset = round(max(gap_offset, price_fraction_offset), symbol_info.digits)
        # _validate_market_sl_tp() requires sl < price < tp for BUY, tp < price < sl for SELL --
        # opposite placement of the same offset around reference_price.
        if SIDE == "BUY":
            sl = round(reference_price - offset, symbol_info.digits)
            tp = round(reference_price + offset, symbol_info.digits)
        else:
            sl = round(reference_price + offset, symbol_info.digits)
            tp = round(reference_price - offset, symbol_info.digits)
        volume = symbol_info.volume_min

        print(f"\n=== Live tick: bid={tick.bid}, ask={tick.ask} ===")
        print(f"=== symbol_info: digits={symbol_info.digits}, point={symbol_info.point}, "
              f"stops_level={symbol_info.stops_level}, freeze_level={symbol_info.freeze_level}, "
              f"volume_min={volume} ===")
        print(f"=== Computed: side={SIDE}, reference_price={reference_price}, gap={gap}, "
              f"gap_offset={gap_offset} ({GAP_SAFETY_MULTIPLIER}x gap), "
              f"price_fraction_offset={price_fraction_offset} "
              f"({MIN_SL_TP_FRACTION_OF_PRICE:.1%} of price), offset={offset} (max of the two), "
              f"sl={sl}, tp={tp}, volume={volume} ===")

        order_plan = OrderPlan(
            symbol=SYMBOL, order_type="MARKET", side=SIDE, volume=volume, price=reference_price,
            sl=sl, tp=tp, deviation=150, magic=SMOKE_TEST_MAGIC,
            comment=f"phase6_step7_market_{SIDE.lower()}_smoke_test",
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

        # No magic filter here either -- see the "before" read's comment above.
        positions_after = await account.get_positions(symbol=SYMBOL)
        still_present = [p for p in positions_after if p.ticket == result.ticket]
        print(f"\n=== Live positions on {SYMBOL} (after, all magics): {len(positions_after)}; "
              f"ticket={result.ticket} still present: {len(still_present) == 1} ===")
        for p in positions_after:
            print(p)

    print("\n=== Done. Single attempt, no retry. No position left open. ===")


if __name__ == "__main__":
    asyncio.run(main())
