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

PHASE 9 STEP 5 (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md): the real daily-loss kill-switch is now
WIRED into both of should_stop()'s call sites below, via _daily_loss_decision_for_cycle() -- one
real get_deals call per loop iteration (not per guard check; the same decision, computed once at
the top of each iteration, is reused for both the pre-cycle check and the during-wait check),
short-circuited to zero real calls when MAX_DAILY_LOSS is None (the default -- see the constant
below). A failure to compute the decision (e.g. get_deals raising) FAILS CLOSED: treated as a
breach, stopping the loop, rather than silently skipping the check and continuing -- a
safety-critical gate that can't confirm safety should stop, not proceed on blind trust. Building
this wiring is NOT the same as running the Step 5 smoke test: MAX_DAILY_LOSS defaults to None
(kill-switch present but inert), so today's behavior is otherwise unaffected until that constant
is deliberately set to a real, tiny threshold for an explicitly-approved live run.

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
- All of this script's own status output goes through the logging module (get_logger(...), not
  print()) -- found, while diagnosing the first run, that plain print() is fully block-buffered
  when stdout isn't a TTY (true for a backgrounded/redirected run), so console/captured output
  could silently lag actual progress by minutes while logging-module records (which flush per
  record) appeared immediately. Routing everything through logging fixes the lag AND ensures
  every status line reaches the file log too (which previously only captured logging-module
  records, missing all of this script's own print()-based reporting).
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
from mt5_mcp_trading.domain.models import RiskDecision
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.live_performance import compute_daily_loss_decision
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.pipeline.grid_cycle import GridCycleError, run_grid_cycle
from mt5_mcp_trading.pipeline.loop_control import LoopLimits, should_stop
from mt5_mcp_trading.pipeline.runner_cycle import run_runner_cycle
from mt5_mcp_trading.risk.daily_loss_guard import (
    DailyLossLimitConfig,
    check_daily_loss_limit,
    daily_reset_boundary,
)
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.state.store import StateStore
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

# Phase 9 Step 5's kill-switch config (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md). MAX_DAILY_LOSS
# deliberately defaults to None -- off, per every other Optional guard field in this codebase --
# until a real smoke-test threshold is explicitly decided and approved as its own separate step;
# building this wiring does not itself authorize running a live trigger test. RESET_HOUR_UTC is
# still Step 2's unreviewed placeholder default, carried forward unresolved.
MAX_DAILY_LOSS: float | None = None
RESET_HOUR_UTC = 0
DAILY_LOSS_CONFIG = DailyLossLimitConfig(max_daily_loss=MAX_DAILY_LOSS, reset_hour_utc=RESET_HOUR_UTC)

_logger = get_logger("mt5_mcp_trading.scripts.pipeline_loop")


def _setup_file_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"pipeline_loop_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return log_path


async def _run_one_cycle(market_data, account, executor, state_store, cycle_num: int) -> bool:
    """Runs grid then runner once each, independently isolated (decision 1). Returns True only
    if BOTH completed without raising -- caller stops the loop on False (decision 3)."""
    ok = True

    _logger.info("[cycle %d] run_grid_cycle(%r, magic=%d)", cycle_num, SYMBOL, GRID_MAGIC)
    try:
        results = await run_grid_cycle(
            market_data=market_data, account=account, executor=executor,
            symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            grid_config=GridStrategyConfig(), money_config=MoneyConfig(),
            caps=CAPS, magic=GRID_MAGIC, state_store=state_store,
        )
    except GridCycleError as exc:
        _logger.info("  GridCycleError: %d side(s) raised, %d side(s) completed",
                      len(exc.errors), len(exc.completed_results))
        for side, error in exc.errors:
            _logger.info("    FAILED side=%s: %r", side, error)
        for result in exc.completed_results:
            _logger.info("    completed: %s", result)
        ok = False
    except Exception as exc:
        _logger.info("  run_grid_cycle() raised: %r", exc)
        ok = False
    else:
        if results:
            for result in results:
                _logger.info("  %s", result)
        else:
            _logger.info("  (nothing submitted -- rejected by a risk guard, or a LIMIT price "
                          "couldn't be normalized far enough from the market)")

    _logger.info("[cycle %d] run_runner_cycle(%r, magic=%d)", cycle_num, SYMBOL, RUNNER_MAGIC)
    try:
        result = await run_runner_cycle(
            market_data=market_data, account=account, executor=executor,
            symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            runner_config=RunnerStrategyConfig(), money_config=MoneyConfig(),
            caps=CAPS, magic=RUNNER_MAGIC, state_store=state_store,
        )
    except Exception as exc:
        _logger.info("  run_runner_cycle() raised: %r", exc)
        ok = False
    else:
        _logger.info("  %s", result if result is not None else
                      "(FLAT signal or rejected by a risk guard, nothing submitted)")

    return ok


async def _compute_daily_loss_decision(
    client, state_store: StateStore, config: DailyLossLimitConfig,
) -> RiskDecision:
    """The real computation (Phase 9 Step 5) -- CAN raise (a real get_deals call is involved);
    callers must not treat this as safe to call unguarded. Short-circuits to zero real MCP calls
    when config.max_daily_loss is None (check_daily_loss_limit() always approves in that case
    regardless of the P&L figure -- see risk/daily_loss_guard.py), so leaving the kill-switch off
    costs nothing extra per cycle."""
    if config.max_daily_loss is None:
        return check_daily_loss_limit(0.0, config)
    now = datetime.now(timezone.utc)
    boundary = daily_reset_boundary(now, config.reset_hour_utc)
    deals = await McpDealHistoryReader(client).get_deals(from_date=boundary.strftime("%Y-%m-%d"))
    trusted_ids = {r.ticket for r in state_store.all_closed()}
    return compute_daily_loss_decision(deals, trusted_ids, now, config)


async def _daily_loss_decision_for_cycle(
    client, state_store: StateStore, config: DailyLossLimitConfig,
) -> RiskDecision:
    """Never raises -- FAILS CLOSED: a computation failure (e.g. get_deals raising) is treated
    as a breach, stopping the loop, rather than silently skipped. A safety-critical gate that
    can't confirm safety should stop, not proceed on blind trust (Phase 9 Step 5's own scoping
    decision, docs/PHASE9_FORWARD_TEST_CHECKPOINT.md)."""
    try:
        return await _compute_daily_loss_decision(client, state_store, config)
    except Exception as exc:
        _logger.warning("Could not compute daily-loss decision this cycle -- failing closed: %r", exc)
        return RiskDecision(
            approved=False,
            reasons=(f"could not compute daily-loss decision, failing closed: {exc!r}",),
            blocking_guard="daily_loss_computation_error",
        )


async def _wait_for_next_cycle(
    limits: LoopLimits, start_time: float, cycle_num: int,
    daily_loss_decision: RiskDecision | None = None,
) -> bool:
    """Waits CYCLE_INTERVAL_SECONDS in small polled increments. Returns True if a stop was
    requested during the wait (stop-file appeared, or a bound was already reached -- no point
    waiting out the full interval just to stop on the next top-of-loop check anyway), False if
    the full interval elapsed with nothing triggering a stop. `daily_loss_decision` is the SAME
    decision computed once at the top of this cycle's iteration (Phase 9 Step 5) -- not
    re-fetched during the wait, matching "one real get_deals call per iteration, not per guard
    check"."""
    waited = 0.0
    while waited < limits.cycle_interval_seconds:
        reason = should_stop(
            cycle_num=cycle_num, elapsed_seconds=time.monotonic() - start_time,
            stop_file_exists=STOP_FILE.exists(), limits=limits,
            daily_loss_decision=daily_loss_decision,
        )
        if reason is not None:
            _logger.info("Stop requested during inter-cycle wait: %s", reason)
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
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)
    _logger.info("Bounded autonomous loop: max_cycles=%s, max_runtime_minutes=%s, "
                 "cycle_interval_seconds=%s, stop_file=%s",
                 MAX_CYCLES, MAX_RUNTIME_MINUTES, CYCLE_INTERVAL_SECONDS, STOP_FILE)
    _logger.info("Daily loss kill-switch: max_daily_loss=%s, reset_hour_utc=%s (None = off)",
                 DAILY_LOSS_CONFIG.max_daily_loss, DAILY_LOSS_CONFIG.reset_hour_utc)
    _logger.info("Log file: %s", log_path)

    start_time = time.monotonic()
    cycle_num = 0

    try:
        async with demo_execution_session(
            settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
        ) as (client, account, executor, state_store):
            _logger.info("Connected via the same wrapper Claude Code uses, trading_enabled=True")
            market_data = McpMarketDataSource(client)

            while True:
                daily_loss_decision = await _daily_loss_decision_for_cycle(
                    client, state_store, DAILY_LOSS_CONFIG,
                )
                reason = should_stop(
                    cycle_num=cycle_num, elapsed_seconds=time.monotonic() - start_time,
                    stop_file_exists=STOP_FILE.exists(), limits=limits,
                    daily_loss_decision=daily_loss_decision,
                )
                if reason is not None:
                    _logger.info("Stopping before cycle %d: %s", cycle_num + 1, reason)
                    break

                cycle_num += 1
                ok = await _run_one_cycle(market_data, account, executor, state_store, cycle_num)
                if not ok:
                    _logger.info("Cycle %d had an error -- stopping the loop "
                                  "(no error tolerance in this version)", cycle_num)
                    break

                stopped_during_wait = await _wait_for_next_cycle(
                    limits, start_time, cycle_num, daily_loss_decision,
                )
                if stopped_during_wait:
                    break
    except KeyboardInterrupt:
        _logger.info("Ctrl+C received after %d cycle(s) -- stopping cleanly", cycle_num)

    _logger.info("Done. %d cycle(s) run. No cleanup performed -- any resulting order/position "
                 "is intentionally left in place. Manage it via a later cycle or a separate "
                 "explicit action. Full log: %s", cycle_num, log_path)


if __name__ == "__main__":
    asyncio.run(main())
