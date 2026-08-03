#!/usr/bin/env python3
"""
Pipeline wiring (post-Phase 7): the first time run_grid_cycle()/run_runner_cycle() are ever run
against the REAL, order-submitting McpOrderExecutor. Every prior real McpOrderExecutor call
(the three run_demo_execution_*_smoke_test.py scripts) used a hand-built OrderPlan, never the
actual strategy pipeline. The only script that runs run_grid_cycle()/run_runner_cycle() against
real market/account data, run_live_dry_run_pipeline.py, always used DryRunExecutor -- zero real
calls. This script is exactly that script with DryRunExecutor swapped for a real
McpOrderExecutor via execution/composition.py's demo_execution_session() -- nothing else
changed. Config values (symbol, timeframe, magics, strategy configs, exposure caps) are copied
verbatim from run_live_dry_run_pipeline.py / tests/integration/test_*_dry_run_pipeline.py's
"realistic" values, deliberately not invented fresh for this step.

"Pipeline wiring" is not one of this project's numbered phases (0-7) -- AGENTS.md and both the
Phase 6/7 checkpoint docs have always called it out as a separate, later, explicitly-approved
effort. This is that effort's first concrete artifact. Writing this script is not the same as
running it: per this project's established pattern (every Phase 6 step required its own
separate, explicit go-ahead immediately before the live call), actually executing this script
against the real MCP/MT5 connection requires a further, separate approval -- do not run it
automatically just because it exists.

STRATEGY below picks exactly ONE of run_grid_cycle()/run_runner_cycle() per invocation, never
both together -- same "flip a constant, rerun" habit as SIDE in
run_demo_execution_market_smoke_test.py, keeping each run's blast radius as narrow as possible
for this first-ever real wiring. GRID_MAGIC=71101/RUNNER_MAGIC=72101 are the real, registered
strategy magics (state/strategy_registry.py) -- deliberately NOT the smoke tests' throwaway
79999, since this run is meant to exercise the real strategy identity end-to-end, not a
disposable probe.

SAFETY / KEY DIFFERENCE FROM EVERY PRIOR SMOKE-TEST SCRIPT: this script does NOT clean up after
itself. The Phase 6 smoke tests were disposable proofs that deliberately closed whatever they
opened, leaving the account clean. A real grid/runner cycle is the opposite: whatever it
decides to submit (or doesn't -- rejection is a normal, expected outcome, not a failure) is the
actual, intended strategy decision, meant to persist and be managed by LATER cycles/
reconciliation, not undone by this same invocation. Any resulting order/position is left in
place on success; managing it (closing, cancelling) is a separate, later, explicit action.

No new "abort if leftover local state" guard exists here either, unlike the smoke tests' magic-
specific abort check -- that check existed because smoke tests need a clean slate to prove a
one-shot round trip. A real cycle is supposed to see prior cycles' open positions/orders as
normal steady state; McpOrderExecutor's existing internal _current_posture() reconciliation
(NORMAL/MANAGE_ONLY/BLOCKED) is already the correct, existing safety mechanism for that -- this
script only prints local state and live positions/orders before and after, for the human
reviewer's visibility, never as a gate of its own.

- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, overriding whatever
  MT5_MCP_MODE is set to in .env -- same reasoning as every other demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- GridCycleError (raised if either side's submit() raised) is caught and reported --
  .completed_results/.errors are printed, never left to crash with a raw traceback.
  run_runner_cycle() returns None for FLAT/rejected outcomes and propagates any raw submit()
  exception directly (confirmed in Phase 7: no partial-results scenario there) -- also caught
  and reported at this script's level, since this script is meant to be safely re-run by a
  human, not to crash uninformatively.
- Never reads, logs, or prints .env or any credential.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.pipeline.grid_cycle import GridCycleError, run_grid_cycle
from mt5_mcp_trading.pipeline.runner_cycle import run_runner_cycle
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"  # directory: one <ticket>.json file per ticket

STRATEGY = "RUNNER"  # "GRID" or "RUNNER" -- flip to choose which this run exercises, never both

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101  # real, registered strategy magic (state/strategy_registry.py) -- not 79999
RUNNER_MAGIC = 72101
CAPS = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)  # same as run_live_dry_run_pipeline.py


async def _print_visibility(account, state_store, magic: int, label: str) -> None:
    positions = await account.get_positions(symbol=SYMBOL, magic=magic)
    orders = await account.get_orders(symbol=SYMBOL, magic=magic)
    local_open = [r for r in state_store.all_open() if r.magic == magic]
    print(f"\n=== {label}: live positions(magic={magic})={len(positions)}, "
          f"live pending orders(magic={magic})={len(orders)}, "
          f"local open records(magic={magic})={len(local_open)} ===")
    for p in positions:
        print(f"  position: {p}")
    for o in orders:
        print(f"  pending order: {o}")
    for r in local_open:
        print(f"  local record: {r}")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== STRATEGY={STRATEGY!r} -- this run submits through run_grid_cycle() or "
          f"run_runner_cycle() only, never both ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        magic = GRID_MAGIC if STRATEGY == "GRID" else RUNNER_MAGIC
        await _print_visibility(account, state_store, magic, "BEFORE")

        market_data = McpMarketDataSource(client)

        if STRATEGY == "GRID":
            print(f"\n=== run_grid_cycle({SYMBOL!r}, magic={GRID_MAGIC}) against the real "
                  f"McpOrderExecutor -- ONE cycle, no retry ===")
            try:
                results = await run_grid_cycle(
                    market_data=market_data, account=account, executor=executor,
                    symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
                    grid_config=GridStrategyConfig(), money_config=MoneyConfig(),
                    caps=CAPS, magic=GRID_MAGIC,
                )
            except GridCycleError as exc:
                print(f"\n=== GridCycleError: {len(exc.errors)} side(s) raised, "
                      f"{len(exc.completed_results)} side(s) completed ===")
                for side, error in exc.errors:
                    print(f"  FAILED side={side}: {error!r}")
                for result in exc.completed_results:
                    print(f"  completed: {result}")
            else:
                if results:
                    for result in results:
                        print(f"\n=== ExecutionResult ===\n{result}")
                else:
                    print("\n=== Nothing submitted (rejected by a risk guard, or a LIMIT price "
                          "couldn't be normalized far enough from the market) ===")
        else:
            print(f"\n=== run_runner_cycle({SYMBOL!r}, magic={RUNNER_MAGIC}) against the real "
                  f"McpOrderExecutor -- ONE cycle, no retry ===")
            try:
                result = await run_runner_cycle(
                    market_data=market_data, account=account, executor=executor,
                    symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
                    runner_config=RunnerStrategyConfig(), money_config=MoneyConfig(),
                    caps=CAPS, magic=RUNNER_MAGIC,
                )
            except Exception as exc:
                print(f"\n=== run_runner_cycle() raised: {exc!r} ===")
            else:
                print(result if result is not None else
                      "\n=== FLAT signal or rejected by a risk guard, nothing submitted ===")

        await _print_visibility(account, state_store, magic, "AFTER")

    print("\n=== Done. No cleanup performed -- any resulting order/position is intentionally "
          "left in place. Manage it via a later cycle or a separate explicit action. ===")


if __name__ == "__main__":
    asyncio.run(main())
