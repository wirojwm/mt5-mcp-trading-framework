#!/usr/bin/env python3
"""
Bounded autonomous loop (post-pipeline-wiring): the first script in this project that runs
multiple cycles against the real, order-submitting McpOrderExecutor WITHOUT a human approving
each individual cycle -- only the initial launch is the human go-ahead. Every prior real
McpOrderExecutor action, including every scripts/run_demo_execution_pipeline_cycle.py run, has
required its own separate approval immediately before that specific call. This changes that for
whatever happens *during* the run, bounded by hard limits below -- it does not change anything
about the safety of any individual submit()/cancel()/close_position() call itself (every existing
guard: demo-account gates, exposure caps, duplicate-order checks, reconciliation posture, is
still enforced fresh on every single call, exactly as before).

No daily-shutdown/kill-switch/circuit-breaker concept existed anywhere in this codebase before
this script (confirmed: risk/__init__.py explicitly says these "do not exist in the legacy
project... would be new functionality, not a migration") -- everything below is new, not an
extension of something already proven. The one piece of genuinely new DECISION logic (when to
stop) lives in pipeline/loop_control.py as a pure, unit-tested function -- this script is a thin
orchestration shell around it, matching every sibling script's shape.

FOUR STRUCTURAL DESIGN DECISIONS (explicitly approved before this was written -- see
docs/PIPELINE_WIRING_CHECKPOINT.md, "Step 14"):
1. STRATEGY SCOPE: runs BOTH run_grid_cycle() and run_runner_cycle() every cycle, sequentially.
   One raising never prevents the other from being attempted that same cycle -- mirrors
   GridCycleError's own per-side isolation philosophy, one level up.
2. STOP MECHANISM: a stop-file (STOP_FILE below) checked before every cycle AND polled every
   POLL_INTERVAL_SECONDS during the inter-cycle wait (so a stop request takes effect within
   ~POLL_INTERVAL_SECONDS, not the full CYCLE_INTERVAL_SECONDS), plus clean Ctrl+C handling.
3. ERROR HANDLING ACROSS CYCLES: the loop stops immediately after any cycle in which EITHER
   strategy raised -- no error-tolerance or retry in this first version. A single unexpected
   exception (dropped connection, etc.) ends the run; a human decides whether to relaunch.
4. CONNECTION MODEL: one long-lived demo_execution_session() for the entire run. A dropped
   connection is fatal (caught by decision 3 above, which stops the loop) -- no reconnect logic
   exists or is attempted.

SAFETY:
- Explicitly constructs Settings with mode=DEMO_EXECUTION IN CODE, same as every other
  demo_execution_session() script.
- require_demo_account_kind() (the reliable, env-sourced hard gate) is enforced by
  demo_execution_session() before anything else is constructed, and re-enforced by
  McpOrderExecutor before every single submit()/cancel()/close_position() call, every cycle.
- CYCLE_INTERVAL_SECONDS/MAX_CYCLES/MAX_RUNTIME_MINUTES below are conservative defaults for a
  first-ever run of this capability (at most ~1 hour of actual cycling under a 90-minute hard
  ceiling) -- adjustable constants, same convention as SYMBOL/STRATEGY in sibling scripts, meant
  to be reviewed and only loosened after watching a full run's results.
- ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06) (same as
  run_demo_execution_pipeline_cycle.py) bounds standing exposure per symbol regardless of how
  many cycles run -- at 0.01 lot/order that's at most 6 open grid orders before the cap blocks
  more, independent of MAX_CYCLES.
- No cleanup of submitted orders/positions at any point, during or after the run -- matches
  run_demo_execution_pipeline_cycle.py's established "a real cycle's result persists, managed by
  a later cycle or a separate explicit action" design. This script does not close anything it
  opens, ever.
- Writes a per-run log file to var/logs/ (in addition to console output) so an unattended run
  leaves a durable, reviewable record regardless of how the process was launched --
  monitoring/logging_setup.py's configure_logging() itself remains console-only and untouched;
  this is additive, scoped to this one script.
- Never reads, logs, or prints .env or any credential.

To stop a running loop: create the file at STOP_FILE's path (e.g. `type nul >
var\\STOP_PIPELINE_LOOP` on Windows, `touch var/STOP_PIPELINE_LOOP` elsewhere), or press Ctrl+C
in the running terminal.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.logging_setup import configure_logging
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.pipeline.grid_cycle import GridCycleError, run_grid_cycle
from mt5_mcp_trading.pipeline.loop_control import LoopLimits, should_stop
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
STOP_FILE = PROJECT_ROOT / "var" / "STOP_PIPELINE_LOOP"
LOG_DIR = PROJECT_ROOT / "var" / "logs"

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101  # real, registered strategy magic (state/strategy_registry.py) -- not 79999
RUNNER_MAGIC = 72101
CAPS = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)  # same as run_live_dry_run_pipeline.py

CYCLE_INTERVAL_SECONDS = 300.0   # 5 minutes between cycles
POLL_INTERVAL_SECONDS = 5.0      # how often to re-check the stop file while waiting
MAX_CYCLES = 12                  # hard ceiling regardless of runtime
MAX_RUNTIME_MINUTES = 90.0       # hard ceiling regardless of cycle count


def _setup_file_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"pipeline_loop_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return log_path


async def _run_one_cycle(market_data, account, executor, cycle_num: int) -> bool:
    """Runs grid then runner once each, independently isolated (decision 1). Returns True only
    if BOTH completed without raising -- caller stops the loop on False (decision 3)."""
    ok = True

    print(f"\n=== [cycle {cycle_num}] run_grid_cycle({SYMBOL!r}, magic={GRID_MAGIC}) ===")
    try:
        results = await run_grid_cycle(
            market_data=market_data, account=account, executor=executor,
            symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            grid_config=GridStrategyConfig(), money_config=MoneyConfig(),
            caps=CAPS, magic=GRID_MAGIC,
        )
    except GridCycleError as exc:
        print(f"  GridCycleError: {len(exc.errors)} side(s) raised, "
              f"{len(exc.completed_results)} side(s) completed")
        for side, error in exc.errors:
            print(f"    FAILED side={side}: {error!r}")
        for result in exc.completed_results:
            print(f"    completed: {result}")
        ok = False
    except Exception as exc:
        print(f"  run_grid_cycle() raised: {exc!r}")
        ok = False
    else:
        if results:
            for result in results:
                print(f"  {result}")
        else:
            print("  (nothing submitted -- rejected by a risk guard, or a LIMIT price "
                  "couldn't be normalized far enough from the market)")

    print(f"=== [cycle {cycle_num}] run_runner_cycle({SYMBOL!r}, magic={RUNNER_MAGIC}) ===")
    try:
        result = await run_runner_cycle(
            market_data=market_data, account=account, executor=executor,
            symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            runner_config=RunnerStrategyConfig(), money_config=MoneyConfig(),
            caps=CAPS, magic=RUNNER_MAGIC,
        )
    except Exception as exc:
        print(f"  run_runner_cycle() raised: {exc!r}")
        ok = False
    else:
        print(f"  {result}" if result is not None else
              "  (FLAT signal or rejected by a risk guard, nothing submitted)")

    return ok


async def _wait_for_next_cycle(limits: LoopLimits, start_time: float, cycle_num: int) -> bool:
    """Waits CYCLE_INTERVAL_SECONDS in small polled increments. Returns True if a stop was
    requested during the wait (stop-file appeared, or a bound was already reached -- no point
    waiting out the full interval just to stop on the next top-of-loop check anyway), False if
    the full interval elapsed with nothing triggering a stop."""
    waited = 0.0
    while waited < limits.cycle_interval_seconds:
        reason = should_stop(
            cycle_num=cycle_num, elapsed_seconds=time.monotonic() - start_time,
            stop_file_exists=STOP_FILE.exists(), limits=limits,
        )
        if reason is not None:
            print(f"\n=== Stop requested during inter-cycle wait: {reason} ===")
            return True
        step = min(limits.poll_interval_seconds, limits.cycle_interval_seconds - waited)
        await asyncio.sleep(step)
        waited += step
    return False


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    # configure_logging() must run BEFORE _setup_file_logging(): logging.basicConfig() (inside
    # configure_logging()) is a no-op if the root logger already has a handler, so adding the
    # file handler first would silently suppress console setup/level configuration.
    configure_logging(settings.log_level)
    log_path = _setup_file_logging()

    limits = LoopLimits(
        max_cycles=MAX_CYCLES, max_runtime_seconds=MAX_RUNTIME_MINUTES * 60.0,
        cycle_interval_seconds=CYCLE_INTERVAL_SECONDS, poll_interval_seconds=POLL_INTERVAL_SECONDS,
    )
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")
    print(f"=== Bounded autonomous loop: max_cycles={MAX_CYCLES}, "
          f"max_runtime_minutes={MAX_RUNTIME_MINUTES}, cycle_interval_seconds="
          f"{CYCLE_INTERVAL_SECONDS}, stop_file={STOP_FILE} ===")
    print(f"=== Log file: {log_path} ===")

    start_time = time.monotonic()
    cycle_num = 0

    try:
        async with demo_execution_session(
            settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
        ) as (client, account, executor, state_store):
            print("=== Connected via the same wrapper Claude Code uses, trading_enabled=True ===")
            market_data = McpMarketDataSource(client)

            while True:
                reason = should_stop(
                    cycle_num=cycle_num, elapsed_seconds=time.monotonic() - start_time,
                    stop_file_exists=STOP_FILE.exists(), limits=limits,
                )
                if reason is not None:
                    print(f"\n=== Stopping before cycle {cycle_num + 1}: {reason} ===")
                    break

                cycle_num += 1
                ok = await _run_one_cycle(market_data, account, executor, cycle_num)
                if not ok:
                    print(f"\n=== Cycle {cycle_num} had an error -- stopping the loop "
                          f"(no error tolerance in this version) ===")
                    break

                stopped_during_wait = await _wait_for_next_cycle(limits, start_time, cycle_num)
                if stopped_during_wait:
                    break
    except KeyboardInterrupt:
        print(f"\n=== Ctrl+C received after {cycle_num} cycle(s) -- stopping cleanly ===")

    print(f"\n=== Done. {cycle_num} cycle(s) run. No cleanup performed -- any resulting "
          f"order/position is intentionally left in place. Manage it via a later cycle or a "
          f"separate explicit action. Full log: {log_path} ===")


if __name__ == "__main__":
    asyncio.run(main())
