#!/usr/bin/env python3
"""
Phase 6, Step 4: the first live call this project ever makes through a real,
trading-capable McpOrderExecutor. Places a single LIMIT order, verifies it, then cancels it --
the smallest possible real action that proves the whole path (settings -> composition root ->
ToolRegistry(trading_enabled=True) -> McpOrderExecutor -> real retcode parsing -> state/
recording -> verification) actually works end-to-end.

See docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md for full context. Run only once, with
explicit approval, reviewed live -- do not add this to any automated/scheduled run.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, overriding whatever
  MT5_MCP_MODE is set to in .env -- this script's intent is self-evident and doesn't depend on
  remembering to flip a shared env var (which could otherwise dangerously leak into any other
  script run against the same .env).
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed -- see mt5_adapter/safety.py.
- Order is a LIMIT BUY at ~10% below the current bid, computed live and rounded to the
  symbol's real digits -- cannot fill during this script's short runtime.
- Volume is the symbol's live volume_min (the smallest size the broker allows).
- magic=SMOKE_TEST_MAGIC (79999) is deliberately outside the 71101 (grid) / 72101 (runner)
  range used elsewhere in this project, so this order is unambiguous in any later audit of
  local state or MT5 history.
- The order is cancelled in a try/finally, so cancellation is attempted even if an assertion
  above it fails mid-script.
- McpOrderExecutor.submit()/cancel() both re-check require_demo_account_kind before doing
  anything, and refuse if the local state posture is anything but NORMAL (or, for cancel, if
  the ticket can't be attributed) -- see state/policy.py.
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"  # directory: one <ticket>.json file per ticket

SYMBOL = "BTCUSD"
SMOKE_TEST_MAGIC = 79999  # deliberately outside the 71101/72101 grid/runner range
PRICE_FRACTION_BELOW_BID = 0.90  # ~10% below current bid -- cannot fill during this script


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        market_data = McpMarketDataSource(client)
        tick = await market_data.get_tick(SYMBOL)
        symbol_info = await market_data.get_symbol_info(SYMBOL)
        price = round(tick.bid * PRICE_FRACTION_BELOW_BID, symbol_info.digits)
        volume = symbol_info.volume_min

        print(f"\n=== Live tick: bid={tick.bid}, ask={tick.ask} ===")
        print(f"=== Computed LIMIT price: {price} ({(1 - PRICE_FRACTION_BELOW_BID):.0%} "
              f"below bid, volume={volume}) ===")

        order_plan = OrderPlan(
            symbol=SYMBOL, order_type="LIMIT", side="BUY", volume=volume, price=price,
            sl=0.0, tp=0.0, deviation=150, magic=SMOKE_TEST_MAGIC,
            comment="phase6_smoke_test",
        )

        print(f"\n=== submit({order_plan}) ===")
        result = await executor.submit(order_plan)
        print(result)

        try:
            assert result.success, f"submit failed: retcode={result.retcode}"
            assert result.verified, "submit succeeded but could not be verified against real MT5 state"
            print("\n=== PASSED: order submitted and verified against real MT5 state ===")
        finally:
            if result.ticket is not None:
                print(f"\n=== Cleanup: cancelling ticket={result.ticket} ===")
                print(f"state before cancel: {state_store.lookup(result.ticket)}")

                cancel_result = await executor.cancel(result.ticket)
                print(cancel_result)

                print(f"state after cancel: {state_store.lookup(result.ticket)}")
            else:
                print("\nNo ticket was returned -- nothing to cancel.")

    print("\n=== Done. ===")


if __name__ == "__main__":
    asyncio.run(main())
