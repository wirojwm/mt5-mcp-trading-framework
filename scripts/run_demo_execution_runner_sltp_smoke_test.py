#!/usr/bin/env python3
"""
Live verification of the runner SL/TP fix (docs/PIPELINE_WIRING_CHECKPOINT.md, "Step 5"):
calls the REAL run_runner_cycle() (not a hand-built OrderPlan) against the real
McpOrderExecutor, so this exercises the actual fixed code path --
strategy/runner.py's compute_stop_distances() -> pipeline/runner_cycle.py's sl/tp computation
-> order_planning.build_order_plan() -> McpOrderExecutor._submit_market()'s mandatory SL/TP
attach -- then independently re-verifies the live position's actual sl/tp, then cleans up.

Unlike scripts/run_demo_execution_pipeline_cycle.py (the "real" pipeline-wiring script, which
deliberately does NOT clean up after itself), this IS a disposable smoke test, mirroring every
prior Phase 6 smoke-test script: SMOKE_TEST_MAGIC=79999 (deliberately outside the 71101/72101
grid/runner range, so it can never be mistaken for a real strategy position), one submission,
independent verification, then close on full success only -- leaving the account clean.

Run only once, with explicit approval, reviewed live -- do not add this to any
automated/scheduled run.

Signal is whatever runner_signal() actually computes from real live bars (MACD sign) -- not
controllable by this script, so the resulting side (BUY/SELL) is not knowable in advance. If
FLAT (or risk-rejected), run_runner_cycle() returns None and nothing is submitted; this script
reports that and stops -- no retry, matching this project's "one attempt, no automatic retry"
convention throughout. A FLAT outcome is a legitimate, expected result of live market
conditions at run time, not a failure of the fix.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed.
- Aborts before running the cycle if a LOCAL state record for SMOKE_TEST_MAGIC is still
  OPEN/OPEN_UNPROTECTED -- same leftover-state guard as run_demo_execution_market_smoke_test.py,
  checked against StateStore (not a live-position magic filter -- MT5 always reports magic=0 on
  positions this project places, confirmed live in Phase 6).
- Volume: MoneyConfig(lot_size_mode="fixed", fixed_lot=<symbol's live volume_min>) -- the
  smallest size the broker allows, read live before the cycle runs. One minimum-lot test only.
- Exactly ONE run_runner_cycle() call, which itself makes at most one executor.submit() call
  (confirmed by Phase 7's own tests: run_runner_cycle has no partial-results/retry scenario).
  McpOrderExecutor.submit() internally makes exactly one place_market_order call and, only if
  that succeeds, exactly one modify_position call for SL/TP attachment -- no retry of either,
  ever, regardless of outcome.
- If SlTpAttachmentFailedError is raised, THIS SCRIPT DOES NOT ATTEMPT ANY CLEANUP -- per the
  established Phase 6 Step 6 design, a failed attach requires its own separate, explicitly
  approved recovery action. The script reports the ticket and stops.
- Only on full success (position opened AND SL/TP confirmed attached by McpOrderExecutor's own
  internal live re-read) does this script proceed to its own designed cleanup: one
  close_position() call, then an independent live re-read confirming absence -- mirroring every
  prior smoke test's "prove it round-trips, then leave the account clean" pattern.
- Independently re-reads the live position's actual sl/tp via account.get_positions() after
  opening (not just trusting ExecutionResult.verified) -- a second, separate confirmation that
  MT5 genuinely attached what was requested, printed side by side with the requested values.
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
from mt5_mcp_trading.mt5_adapter.mcp_order_executor import SlTpAttachmentFailedError
from mt5_mcp_trading.pipeline.runner_cycle import run_runner_cycle
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"  # directory: one <ticket>.json file per ticket

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
SMOKE_TEST_MAGIC = 79999  # deliberately outside the 71101/72101 grid/runner range -- same
# convention as every Phase 6 smoke test, distinct from the "real" 72101 used by
# scripts/run_demo_execution_pipeline_cycle.py's pipeline-wiring runs
CAPS = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")

        positions_before = await account.get_positions(symbol=SYMBOL)
        print(f"\n=== Live positions on {SYMBOL} (before, all magics): {len(positions_before)} ===")
        for p in positions_before:
            print(f"  {p}")

        leftover_records = [r for r in state_store.all_open() if r.magic == SMOKE_TEST_MAGIC]
        print(f"\n=== Local state records for magic={SMOKE_TEST_MAGIC} still open: "
              f"{len(leftover_records)} ===")
        for r in leftover_records:
            print(f"  {r}")
        if leftover_records:
            print(f"\nABORT: {len(leftover_records)} local record(s) for magic={SMOKE_TEST_MAGIC} "
                  f"are still OPEN/OPEN_UNPROTECTED -- likely a leftover from a previous "
                  f"incomplete run. Resolve that separately before running this script again. "
                  f"No cycle run.")
            return

        market_data = McpMarketDataSource(client)
        symbol_info = await market_data.get_symbol_info(SYMBOL)
        volume = symbol_info.volume_min
        print(f"\n=== symbol_info: digits={symbol_info.digits}, point={symbol_info.point}, "
              f"volume_min={volume} ===")

        money_config = MoneyConfig(lot_size_mode="fixed", fixed_lot=volume)
        runner_config = RunnerStrategyConfig()  # defaults: atr_period=14, sl_atr_mult=1.5,
        # tp_atr_mult=3.0, min_stop_distance_points=10.0 -- the fixed code under test

        print(f"\n=== run_runner_cycle({SYMBOL!r}, magic={SMOKE_TEST_MAGIC}) against the real "
              f"McpOrderExecutor -- ONE cycle, no retry ===")
        try:
            result = await run_runner_cycle(
                market_data=market_data, account=account, executor=executor,
                symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
                runner_config=runner_config, money_config=money_config,
                caps=CAPS, magic=SMOKE_TEST_MAGIC, state_store=state_store,
            )
        except SlTpAttachmentFailedError as exc:
            print(f"\n=== SL/TP ATTACHMENT FAILED: ticket={exc.ticket}, reason={exc.reason!r}, "
                  f"retcode={exc.retcode} ===")
            print("Position is OPEN and UNPROTECTED on the account. Per the established Phase 6 "
                  "Step 6 design, NO automatic retry or close is attempted by this script. "
                  "Recovery requires its own separate, explicitly approved action -- do not "
                  "re-run this script until that ticket is resolved.")
            return

        if result is None:
            print("\n=== FLAT signal or rejected by a risk guard -- nothing submitted. This is a "
                  "legitimate outcome of live market conditions, not a failure of the fix. "
                  "Nothing to verify or clean up. ===")
            return

        plan = result.order_plan
        print(f"\n=== ExecutionResult ===\n{result}")
        print(f"\n=== Requested by the fixed code: side={plan.side}, volume={plan.volume}, "
              f"price={plan.price}, sl={plan.sl}, tp={plan.tp} ===")

        if not result.success:
            print("\n=== Order was rejected (retcode not TRADE_RETCODE_DONE) -- nothing was "
                  "opened, nothing to clean up. ===")
            return

        assert result.ticket is not None  # success=True from _submit_market always carries one
        assert plan.sl > 0 and plan.tp > 0, "REGRESSION: sl/tp must be non-zero"
        if plan.side == "BUY":
            assert plan.sl < plan.price < plan.tp, "REGRESSION: wrong BUY sl/tp ordering"
        else:
            assert plan.tp < plan.price < plan.sl, "REGRESSION: wrong SELL sl/tp ordering"

        print(f"\n=== PASSED: MARKET order opened (ticket={result.ticket}) with non-zero, "
              f"correctly-ordered SL/TP, confirmed attached against real MT5 state "
              f"(verified={result.verified}) ===")

        # Independent live re-verification -- don't just trust ExecutionResult.verified.
        positions_after_open = await account.get_positions(symbol=SYMBOL)
        live_position = next((p for p in positions_after_open if p.ticket == result.ticket), None)
        print(f"\n=== Independent live re-read of ticket={result.ticket}: {live_position} ===")
        if live_position is not None:
            print(f"=== Live sl={live_position.sl}, tp={live_position.tp} vs requested "
                  f"sl={plan.sl}, tp={plan.tp} ===")

        # This script's own designed cleanup -- reached ONLY on full success.
        print(f"\n=== Cleanup: closing ticket={result.ticket} (one attempt, no retry) ===")
        print(f"state before close: {state_store.lookup(result.ticket)}")

        close_result = await executor.close_position(result.ticket)
        print(f"\n=== ExecutionResult (close) ===\n{close_result}")

        print(f"state after close: {state_store.lookup(result.ticket)}")

        positions_final = await account.get_positions(symbol=SYMBOL)
        still_present = any(p.ticket == result.ticket for p in positions_final)
        print(f"\n=== Live positions on {SYMBOL} (after close, all magics): {len(positions_final)}; "
              f"ticket={result.ticket} still present: {still_present} ===")
        for p in positions_final:
            print(f"  {p}")

    print("\n=== Done. Single cycle, single close attempt, no retry either way. ===")


if __name__ == "__main__":
    asyncio.run(main())
