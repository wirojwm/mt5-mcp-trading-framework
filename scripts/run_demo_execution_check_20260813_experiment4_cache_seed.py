#!/usr/bin/env python3
"""
Phase 8 continuation, Experiment 4 data step (approved 2026-08-13): seeds M15 and H1 BTCUSD
caches so the same random-walk test methodology from Experiment 3 can be re-run at longer
timeframes. Mirrors scripts/run_demo_execution_historical_data_cache_seed.py's exact
established pattern (same cache module, same merge-not-overwrite safety, same
get_candles_latest-only READ_ONLY call), generalized to loop over both new timeframes instead of
duplicating the script twice.

FETCH_COUNT=60,000 for both -- comfortably above each timeframe's known real depth (Phase 8's
original probe: M15 ~51,000 bars/532 days back to 2025-02-19; H1 ~53,000 bars/2205 days back to
2020-07-22), so get_candles_latest should degrade gracefully to "whatever exists" rather than
hit the harder ~95,000-row response-size ceiling M1's own seed script found empirically (that
ceiling sits well above 60,000, so this request shouldn't need M1's own trial-and-error).

READ-ONLY. Only calls get_candles_latest (via McpMarketDataSource.get_bars(), READ_ONLY-
classified). No `executor` reference anywhere. No production default changed.
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state_historical_data_cache_seed"  # unused, read-only
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "BTCUSD"
TIMEFRAMES = ["M15", "H1"]
FETCH_COUNT = 60_000

_logger = get_logger("mt5_mcp_trading.scripts.experiment4_cache_seed")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store  # deliberately never used -- read-only script

        market_data = McpMarketDataSource(client)

        for timeframe in TIMEFRAMES:
            path = cache_path(CACHE_DIR, SYMBOL, timeframe)
            already_cached = load_bars(path, SYMBOL, timeframe)
            _logger.info("[%s] Cache before this run: %d bar(s)", timeframe, len(already_cached))

            _logger.info("[%s] Requesting %d bars ...", timeframe, FETCH_COUNT)
            fetched = await market_data.get_bars(SYMBOL, timeframe, FETCH_COUNT)
            if not fetched:
                _logger.warning("[%s] Requested %d bars, got 0 back -- likely hit a response-size "
                                 "ceiling. Cache left unchanged for this timeframe.",
                                 timeframe, FETCH_COUNT)
                print(f"\n{timeframe}: FETCH_COUNT={FETCH_COUNT} returned 0 bars -- try smaller.\n")
                continue

            _logger.info("[%s] Fetched %d bar(s): %s -> %s", timeframe, len(fetched),
                         fetched[0].time.isoformat(), fetched[-1].time.isoformat())

            merged = merge_bars(already_cached, fetched)
            save_bars(path, merged)
            print(f"\n=== {timeframe} cache seed ===")
            print(f"cache file: {path}")
            print(f"bars before: {len(already_cached)}  fetched: {len(fetched)}  "
                  f"after (merged): {len(merged)}")
            print(f"range: {merged[0].time.isoformat()} -> {merged[-1].time.isoformat()}")

    print("\n=====================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
