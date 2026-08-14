#!/usr/bin/env python3
"""
XAUUSD signal-edge research, Step 1 (approved 2026-08-14, docs/XAUUSD_SIGNAL_EDGE_CHECKPOINT.md):
seeds offline M1/M15/H1 XAUUSD caches so Steps 2-4 (edge test, random-walk test, cost-inclusive
rule test -- all reusing BTCUSD's already-validated methodology verbatim) can run entirely
offline afterward, exactly the same division of labor the BTCUSD investigation used.

Mirrors two already-established, separately-proven patterns rather than inventing a third:
- scripts/run_demo_execution_historical_data_cache_seed.py's M1 approach: FETCH_COUNT=95,000,
  the empirically-bisected ceiling for this tool (100,000 returns 0 bars outright, 95,000
  succeeds -- a response-size limit, not the terminal's own historical depth, which
  get_candles_latest degrades to gracefully below that ceiling).
- scripts/run_demo_execution_check_20260813_experiment4_cache_seed.py's M15/H1 approach:
  FETCH_COUNT=60,000 for both, comfortably above BTCUSD's own real M15/H1 depth at the time
  (~51,000/~53,000) -- reused as the same starting point for XAUUSD since neither's real depth is
  known in advance; a 0-bar response degrades gracefully into a per-timeframe warning, not a
  crash, so this script does not need to pre-discover XAUUSD's exact depth first.

READ-ONLY throughout. Only calls get_candles_latest (via McpMarketDataSource.get_bars(),
READ_ONLY-classified) -- the same live-call pattern already explicitly authorized once for
XAUUSD specifically (scripts/run_demo_execution_xauusd_symbol_research.py, 2026-08-13). No
`executor` reference anywhere in this file. No order of any kind. No production default changed.
Safe to re-run: merges into whatever's already cached (market_data_cache.merge_bars(), dedups by
timestamp, new wins) rather than overwriting.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars, merge_bars, save_bars
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state_xauusd_cache_seed"  # unused, read-only
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "XAUUSD"
FETCH_COUNTS = {"M1": 95_000, "M15": 60_000, "H1": 60_000}

_logger = get_logger("mt5_mcp_trading.scripts.xauusd_cache_seed")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store  # deliberately never used -- read-only script

        market_data = McpMarketDataSource(client)

        for timeframe, fetch_count in FETCH_COUNTS.items():
            path = cache_path(CACHE_DIR, SYMBOL, timeframe)
            already_cached = load_bars(path, SYMBOL, timeframe)
            _logger.info("[%s] Cache before this run: %d bar(s)", timeframe, len(already_cached))

            _logger.info("[%s] Requesting %d bars ...", timeframe, fetch_count)
            fetched = await market_data.get_bars(SYMBOL, timeframe, fetch_count)
            if not fetched:
                _logger.warning("[%s] Requested %d bars, got 0 back -- likely hit a response-size "
                                 "ceiling. Cache left unchanged for this timeframe.",
                                 timeframe, fetch_count)
                print(f"\n{timeframe}: FETCH_COUNT={fetch_count} returned 0 bars -- try smaller.\n")
                continue

            _logger.info("[%s] Fetched %d bar(s): %s -> %s", timeframe, len(fetched),
                         fetched[0].time.isoformat(), fetched[-1].time.isoformat())

            merged = merge_bars(already_cached, fetched)
            save_bars(path, merged)
            print(f"\n=== {SYMBOL} {timeframe} cache seed ===")
            print(f"cache file: {path}")
            print(f"bars before: {len(already_cached)}  fetched: {len(fetched)}  "
                  f"after (merged): {len(merged)}")
            print(f"range: {merged[0].time.isoformat()} -> {merged[-1].time.isoformat()}")

    print("\n=====================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
