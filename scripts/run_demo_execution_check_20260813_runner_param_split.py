#!/usr/bin/env python3
"""
One-off diagnostic (not part of any scoped deliverable): Phase 8-continuation research into
runner's live-vs-backtest expectancy divergence (Step 6's held-out backtest: -0.100 R at
sl_atr_mult=3.0/tp_atr_mult=6.0; today's live read: -0.607 R across 64 trades).

Hypothesis under test: the 64-trade live sample is NOT parameter-homogeneous. Runner's
sl_atr_mult/tp_atr_mult changed from 1.5/3.0 to 3.0/6.0 when Phase 8's tuning result was adopted
as the production default -- confirmed via AGENTS.md/live-testing-paused memory to have happened
2026-08-05 (the runner_sltp_smoke_test live-verification, ticket 171702598). Every runner trade
SUBMITTED before that date used the OLD config; every trade submitted on/after it used the NEW
(currently-live, backtest-comparable) config. Splits the same real closed-trade join
build_closed_trades() already does, by record.submitted_at relative to that boundary, and reports
metrics for each half separately -- so the aggregate 64-trade number (which blends both configs)
isn't mistaken for a fair comparison to a backtest that used only the new config throughout.

READ-ONLY: same shape as run_demo_execution_live_performance_monitor.py. Only get_deals()
(READ_ONLY-classified). No `executor` reference anywhere in this file. Uses the real production
StateStore path.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r, profit_factor, win_rate
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.live_performance import build_closed_trades, infer_deal_time_offset
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

RUNNER_MAGIC = 72101
# 2026-08-05: date the sl_atr_mult=1.5->3.0 / tp_atr_mult=3.0->6.0 production default change was
# live-verified and adopted (AGENTS.md, Phase 8 "Runner's validated candidate adopted..." entry;
# [[project_live_testing_paused]] memory's 2026-08-05 update, ticket 171702598).
PARAM_CHANGE_DATE = datetime(2026, 8, 5, tzinfo=timezone.utc)
# 2026-08-11T04:42:04Z: exact commit timestamp of f41a5a1 (`git log`), which added the
# min_stop_distance_fraction_of_price=0.01 floor to compute_stop_distances() -- before this,
# runner's SL/TP distance could be far tighter than 1% of price whenever ATR-derived `base` came
# in small, a second, independent confound layered on top of the sl_atr_mult change above.
FLOOR_FIX_DATETIME = datetime(2026, 8, 11, 4, 42, 4, tzinfo=timezone.utc)


def _print_group(label: str, trades: list) -> None:
    print(f"\n--- {label}: {len(trades)} trade(s) ---")
    if not trades:
        print("  (none)")
        return
    print(f"  expectancy_r:  {expectancy_r(trades):+.3f} R")
    print(f"  max_drawdown_r:{max_drawdown_r(trades):.3f} R")
    print(f"  win_rate:      {win_rate(trades):.1%}")
    print(f"  profit_factor: {profit_factor(trades):.3f}")
    print(f"  min sample (30+)?  {'yes' if has_minimum_sample(trades) else 'no'}")
    avg_risk = sum(abs(t.price_open - t.sl) for t in trades) / len(trades)
    avg_price = sum(t.price_open for t in trades) / len(trades)
    print(f"  avg SL distance: {avg_risk:.2f} price units ({avg_risk / avg_price:.3%} of avg price)"
          f" -- sanity check this matches the expected sl_atr_mult regime")
    holding = [(t.closed_at - t.opened_at).total_seconds() / 60 for t in trades]
    print(f"  avg holding time: {sum(holding) / len(holding):.1f} min "
          f"(min={min(holding):.1f}, max={max(holding):.1f})")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    print(f"=== mode={settings.mode.value}, trading_enabled={settings.trading_enabled}, "
          f"mt5_account_kind={settings.mt5_account_kind!r} ===")

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor  # deliberately never used -- read-only diagnostic

        closed_records = state_store.all_closed()
        runner_records = [r for r in closed_records if r.magic == RUNNER_MAGIC]
        earliest = min(r.submitted_at for r in closed_records)
        from_date = earliest.strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc)
        to_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        deal_reader = McpDealHistoryReader(client)
        deals = await deal_reader.get_deals(from_date=from_date, to_date=to_date)
        all_records = state_store.all_records()
        offset = infer_deal_time_offset(all_records, deals) or timedelta(0)

    print(f"\n=== Runner ({RUNNER_MAGIC}) local closed records: {len(runner_records)} ===")
    print(f"Param-change boundary: {PARAM_CHANGE_DATE.isoformat()} "
          f"(sl_atr_mult 1.5->3.0, tp_atr_mult 3.0->6.0)")

    group_a = [r for r in runner_records if r.submitted_at < PARAM_CHANGE_DATE]
    group_b = [r for r in runner_records
               if PARAM_CHANGE_DATE <= r.submitted_at < FLOOR_FIX_DATETIME]
    group_c = [r for r in runner_records if r.submitted_at >= FLOOR_FIX_DATETIME]
    print(f"  group A (sl_atr_mult=1.5, pre-floor-fix): {len(group_a)}")
    print(f"  group B (sl_atr_mult=3.0, PRE floor-fix -- the retcode-10016 bug window): {len(group_b)}")
    print(f"  group C (sl_atr_mult=3.0, POST floor-fix -- matches today's backtest-comparable config): {len(group_c)}")

    result = build_closed_trades(runner_records, deals)
    print(f"\nMatched to real trades: {len(result.trades)}   Skipped: {len(result.skipped)}")
    for skip in result.skipped:
        print(f"  skipped ticket={skip.ticket}: {skip.reason}")

    a_tickets = {r.ticket for r in group_a}
    b_tickets = {r.ticket for r in group_b}
    c_tickets = {r.ticket for r in group_c}
    a_trades = [t for t in result.trades if t.ticket in a_tickets]
    b_trades = [t for t in result.trades if t.ticket in b_tickets]
    c_trades = [t for t in result.trades if t.ticket in c_tickets]

    _print_group("GROUP A: sl_atr_mult=1.5/tp_atr_mult=3.0 (pre-2026-08-05)", a_trades)
    _print_group("GROUP B: sl_atr_mult=3.0/tp_atr_mult=6.0, NO 1% floor (2026-08-05 to 08-11 04:42 UTC)", b_trades)
    _print_group("GROUP C: sl_atr_mult=3.0/tp_atr_mult=6.0, WITH 1% floor (2026-08-11 04:42 UTC onward)", c_trades)
    _print_group("ALL (blended, matches today's monitor read)", list(result.trades))

    print(f"\ndeal_time_offset used: {offset}")
    print("=====================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
