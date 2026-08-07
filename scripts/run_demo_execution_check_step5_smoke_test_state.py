#!/usr/bin/env python3
"""
One-off diagnostic (not part of Phase 9's scoped deliverables): follow-up to the Step 5 live
smoke test run (docs/PHASE9_FORWARD_TEST_CHECKPOINT.md, 2026-08-07). The kill-switch never
tripped over 12 real cycles, but the log shows 3 separate runner (magic=72101) MARKET positions
opening in succession (tickets 171809336, 171809814, 171809876, then 171809948 still open at the
end) -- meaning positions closed between them (check_position_limit's live read is state_store-
cross-referenced, not the broken raw magic filter, so "0 open" is trustworthy) -- yet
_compute_daily_loss_decision()'s get_deals call found ZERO deals every single cycle. That's the
real, unexplained thing this script checks, not assumed either way.

READ-ONLY: get_positions_with_magic/get_pending_orders_with_magic/get_deals are all already
READ_ONLY-classified in metatrader_tools.py. No `executor` reference, no order of any kind.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state"

SYMBOL = "BTCUSD"
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
RUNNER_TICKETS = {171809336, 171809814, 171809876, 171809948}

_logger = get_logger("mt5_mcp_trading.scripts.check_step5_smoke_test_state")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del executor  # deliberately never used -- read-only diagnostic

        positions = await account.get_positions(symbol=SYMBOL)
        orders = await account.get_orders(symbol=SYMBOL)
        deals = await McpDealHistoryReader(client).get_deals(from_date="2026-08-07")
        local_open = [r for r in state_store.all_open()
                      if r.magic in (GRID_MAGIC, RUNNER_MAGIC)]

    print(f"\n=== REAL current open positions on {SYMBOL}: {len(positions)} ===")
    for p in positions:
        print(f"  ticket={p.ticket} side={p.side} volume={p.volume} "
              f"sl={p.sl} tp={p.tp} magic(broker-reported)={p.magic}")

    print(f"\n=== REAL current pending orders on {SYMBOL}: {len(orders)} ===")
    for o in orders:
        print(f"  ticket={o.ticket} side={o.side} price={o.price} magic(broker-reported)={o.magic}")

    print(f"\n=== Local StateStore.all_open() for magics {{71101,72101}}: {len(local_open)} ===")
    for r in local_open:
        print(f"  ticket={r.ticket} magic={r.magic} status={r.status} strategy={r.strategy}")

    print(f"\n=== get_deals(from_date='2026-08-07') queried fresh, now: {len(deals)} deal(s) ===")
    runner_deals = [d for d in deals if d.position_id in RUNNER_TICKETS]
    print(f"  {len(runner_deals)} deal(s) matching this run's 4 runner tickets {sorted(RUNNER_TICKETS)}")
    for d in sorted(runner_deals, key=lambda d: d.time):
        print(f"  position_id={d.position_id} time={d.time.isoformat()} entry={d.entry} "
              f"type={d.type} profit={d.profit} commission={d.commission} swap={d.swap}")
    print("=====================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
