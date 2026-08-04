#!/usr/bin/env python3
"""
Step 18 live verification (docs/PIPELINE_WIRING_CHECKPOINT.md, "Exact next smallest task" #1):
proves, against the real demo account, that the state_store-based magic recovery in
run_grid_cycle()/run_runner_cycle() actually discriminates correctly between grid (magic=71101),
runner (magic=72101), and any other live tickets on the account -- not just against mocks (the
only place it had been exercised before this script).

Read-only: get_positions()/get_orders() only. Places, modifies, and closes NOTHING. Uses
demo_execution_session() only for its existing McpAccountReader/StateStore plumbing -- no
executor.submit()/cancel()/close_position() call appears anywhere in this file.

Method: reproduces, by hand, exactly what pipeline/grid_cycle.py and pipeline/runner_cycle.py's
state_store branch computes internally, side by side with the OLD (buggy) broker-side-magic-
filtered call, for each of the two real registered magics (state/strategy_registry.py):

  OLD (pre-Step-18, still what state_store=None produces): account.get_positions(symbol,
  magic=magic)/get_orders(symbol, magic=magic) -- expected to return EMPTY for both magics
  against real data, per the confirmed root cause (MT5 always reports magic=0 on this
  project's own tickets, docs/mcp_tool_classification.md item 7).

  NEW (Step 18 fix): unfiltered account.get_positions(symbol)/get_orders(symbol), intersected
  with {r.ticket for r in state_store.all_open() if r.magic == magic} -- expected to correctly
  recover each magic's real, live tickets, discriminating grid's from runner's from anything
  else on the account.
"""

from __future__ import annotations

import asyncio
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
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101


async def _check_one_magic(account, state_store, magic: int, label: str) -> None:
    print(f"\n=== magic={magic} ({label}) ===")

    old_positions = await account.get_positions(symbol=SYMBOL, magic=magic)
    old_orders = await account.get_orders(symbol=SYMBOL, magic=magic)
    print(f"  OLD (broker-side magic filter, state_store=None): "
          f"positions={len(old_positions)}, orders={len(old_orders)}")
    if old_positions or old_orders:
        print("  *** UNEXPECTED: broker-side magic filter returned real data -- the magic=0 "
              "quirk this fix targets may no longer be present, re-read the finding. ***")

    local_tickets = {r.ticket for r in state_store.all_open() if r.magic == magic}
    all_positions = await account.get_positions(symbol=SYMBOL)
    all_orders = await account.get_orders(symbol=SYMBOL)
    new_positions = [p for p in all_positions if p.ticket in local_tickets]
    new_orders = [o for o in all_orders if o.ticket in local_tickets]
    open_lots = sum(p.volume for p in new_positions)
    pending_lots = sum(o.volume for o in new_orders)
    print(f"  NEW (Step 18 fix, state_store={{local tickets for magic={magic}}}={len(local_tickets)}): "
          f"positions={len(new_positions)}, orders={len(new_orders)}, "
          f"open_lots={open_lots}, pending_lots={pending_lots}")
    for p in new_positions:
        print(f"    position: {p}")
    for o in new_orders:
        print(f"    pending order: {o}")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses (read-only this run) ===")

        all_positions = await account.get_positions(symbol=SYMBOL)
        all_orders = await account.get_orders(symbol=SYMBOL)
        print(f"\n=== Ground truth: {len(all_positions)} live position(s), "
              f"{len(all_orders)} live pending order(s) on {SYMBOL} (all magics, unfiltered) ===")
        for p in all_positions:
            print(f"  position: {p}")
        for o in all_orders:
            print(f"  pending order: {o}")

        await _check_one_magic(account, state_store, GRID_MAGIC, "grid")
        await _check_one_magic(account, state_store, RUNNER_MAGIC, "runner")

    print("\n=== Done. Read-only -- nothing placed, modified, or closed. ===")


if __name__ == "__main__":
    asyncio.run(main())
