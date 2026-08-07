#!/usr/bin/env python3
"""
Phase 9 Step 4's read-only live performance/drawdown monitor
(docs/PHASE9_FORWARD_TEST_CHECKPOINT.md's Design section, item 3): reports real R-multiple
expectancy/drawdown for whatever this project's real StateStore currently has on record,
computed from a real closed-trade (get_deals) read -- not backtested, not guessed.

READ-ONLY, same risk category as scripts/run_demo_execution_historical_data_probe.py: only
calls get_deals (McpDealHistoryReader.get_deals(), a READ_ONLY-classified tool). No `executor`
reference anywhere in this file -- `executor` from demo_execution_session() is unpacked and
immediately discarded, same pattern as every other read-only script in this project.

Uses the REAL production StateStore path (var/order_state, the same directory
run_demo_execution_pipeline_loop.py and every other real-executor script write to) --
deliberately not a throwaway path, since the entire point is reporting on this project's actual
recorded trading history.

get_deals' `from_date` is derived from the earliest `submitted_at` among the real closed
records themselves (going back to midnight UTC of that day, comfortably inclusive), rather than
a hardcoded guess or the tool's own 30-day default -- guarantees the query window covers every
locally closed record without needing to know in advance how far back this project's real
history goes. Any record the query genuinely still misses (e.g. a deal purged from this MT5
terminal's own local history) shows up honestly as a skip in the report below, never silently
dropped -- see monitoring/live_performance.py's build_closed_trades().

`to_date` is now explicit (`now + 1 day`) -- Phase 9 Step 7 scoping (2026-08-07) found this
script had the SAME bug Step 5's kill-switch had before its own fix: no explicit `to_date` meant
the vendored client's `to_date=None -> datetime.now()` default, combined with `Deal.time`'s
confirmed UTC mislabel (see monitoring/live_performance.py's module docstring), silently narrowed
the window and missed same-day recent closes -- live-confirmed: this script reported byte-for-bit
the same numbers as the 2026-08-06 read even after 35 real, deal-confirmed closes from Step 5's
smoke tests. Fixed the same tested way: a wide-margin fetch plus `infer_deal_time_offset()`
correction, applied to both the trade-matching read below and the kill-switch-preview section.

**Known remaining caveat, NOT fixed here, distinct from the above**: `build_closed_trades()`
still only ever sees `state_store.all_closed()` (status=="CLOSED" locally) -- a real close via
broker-side SL/TP that hasn't yet been explicitly reconciled (via a separate reconciliation
script, `record_closed()`'s only path outside `McpOrderExecutor.close_position()`) stays
invisible to this monitor's trade count, same root cause as the kill-switch's second bug
(`all_records()` vs `all_closed()`) but a materially different fix (`build_closed_trades()`'s own
contract is specifically about already-`CLOSED` records) -- not changed here. Run a reconciliation
pass first if the trade count looks lower than expected.

Also reports realized P&L since the most recent UTC daily-reset boundary
(risk/daily_loss_guard.daily_reset_boundary()) -- the real number Step 2's kill-switch has been
missing since it was built (its own "risks carried forward" note). This script only DISPLAYS
that number; it does NOT call check_daily_loss_limit() or feed anything into
pipeline/loop_control.py -- wiring the kill-switch into a live decision remains its own,
separate, later, explicitly-approved step, same as every other live-affecting change in this
project.

Per this project's own established metric convention (backtest/metrics.py's module docstring,
Phase 8 Step 1): grid and runner are always reported separately, never blended -- blending could
hide one strategy's losing edge behind the other's positive one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.metrics import (
    MIN_TRADES_FOR_A_CLAIM,
    expectancy_r,
    has_minimum_sample,
    max_drawdown_r,
)
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.live_performance import (
    build_closed_trades,
    infer_deal_time_offset,
    realized_pnl_since,
)
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader
from mt5_mcp_trading.risk.daily_loss_guard import daily_reset_boundary

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"  # the REAL, live state directory -- not a probe path

# Locked parameter set (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md, Step 1) -- must stay in sync
# with that table if it's ever updated for a real, separately-approved reason.
STRATEGIES: dict[str, int] = {"grid": 71101, "runner": 72101}

_logger = get_logger("mt5_mcp_trading.scripts.live_performance_monitor")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor  # deliberately never used -- read-only monitor

        closed_records = state_store.all_closed()
        if not closed_records:
            print("\nNo locally closed trades found in StateStore -- nothing to report.\n")
            return

        earliest_submitted = min(r.submitted_at for r in closed_records)
        from_date = earliest_submitted.strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc)
        to_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        _logger.info(
            "Found %d locally closed record(s); requesting real deal history from %s to %s ...",
            len(closed_records), from_date, to_date,
        )

        deal_reader = McpDealHistoryReader(client)
        deals = await deal_reader.get_deals(from_date=from_date, to_date=to_date)
        _logger.info("Got %d real deal(s) from get_deals.", len(deals))
        all_records = state_store.all_records()
        deal_time_offset = infer_deal_time_offset(all_records, deals) or timedelta(0)
        _logger.info("inferred deal_time_offset=%s", deal_time_offset)

    result = build_closed_trades(closed_records, deals)

    print(f"\n=== Live performance monitor ({len(closed_records)} locally closed record(s)) ===")
    print(f"Matched to real trades: {len(result.trades)}   Skipped: {len(result.skipped)}")

    for strategy, magic in STRATEGIES.items():
        trades = [t for t in result.trades if t.magic == magic]
        print(f"\n--- {strategy} (magic={magic}) ---")
        if not trades:
            print("  no matched real closed trades")
            continue
        print(f"  trades:        {len(trades)}")
        print(f"  expectancy_r:  {expectancy_r(trades):+.3f} R")
        print(f"  max_drawdown_r:{max_drawdown_r(trades):.3f} R")
        print(f"  min sample ({MIN_TRADES_FOR_A_CLAIM}+)?  "
              f"{'yes' if has_minimum_sample(trades) else 'no'}")

    unrecognized_magic_trades = [t for t in result.trades if t.magic not in STRATEGIES.values()]
    if unrecognized_magic_trades:
        print(f"\n--- unrecognized magic(s) ---")
        print(f"  {len(unrecognized_magic_trades)} matched trade(s) carry a magic outside "
              f"{sorted(STRATEGIES.values())} -- not attributed to grid or runner, not counted "
              f"above, listed for visibility only.")

    if result.skipped:
        print(f"\n--- skipped local records ({len(result.skipped)}) ---")
        for skip in result.skipped:
            print(f"  ticket={skip.ticket}: {skip.reason}")

    # all_records(), not all_closed() -- mirrors the real kill-switch wiring's own fix (Step 5's
    # second bug): a same-session SL/TP close stays locally "OPEN" until reconciled, and must
    # still count here, same "never trust a stale local status field" reasoning.
    trusted_position_ids = {r.ticket for r in all_records}
    reset_boundary = daily_reset_boundary(now, reset_hour_utc=0)
    since_reset = realized_pnl_since(deals, since=reset_boundary, trusted_position_ids=trusted_position_ids,
                                      deal_time_offset=deal_time_offset)
    print(f"\n--- kill-switch preview (mirrors the real wiring in run_demo_execution_pipeline_loop.py) ---")
    print(f"  realized P&L since {reset_boundary.isoformat()}: {since_reset:+.2f}")
    print("  (display only -- not fed into check_daily_loss_limit() or loop_control.py here)")
    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
