#!/usr/bin/env python3
"""
Phase 8, Step 2's remaining deliverable (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): pulls
real historical bars from the demo connection and persists them into the local cache
(backtest/market_data_cache.py) under var/market_data/ -- var/ is git-ignored (.gitignore),
so this cache is a local, machine-specific artifact, never committed.

Targets BTCUSD M1 only -- matches TIMEFRAME="M1", the only timeframe any real pipeline script
in this project actually trades on live (scripts/run_demo_execution_pipeline_cycle.py,
scripts/run_demo_execution_pipeline_loop.py both use it for both grid and runner). The
historical-data-depth probe (scripts/run_demo_execution_historical_data_probe.py) already
confirmed this demo terminal has at least 50,000 M1 bars (~35 days) available -- more likely
exists but wasn't measured, since that was already enough to comfortably support Phase 8 Step
1's 30-trade-per-strategy minimum sample.

READ-ONLY. Only calls get_candles_latest (via McpMarketDataSource.get_bars(), a
READ_ONLY-classified tool). No symbol trading call, no `executor` reference anywhere in this
file -- `executor`/`state_store` from demo_execution_session() are unpacked and immediately
discarded, same pattern as every other real-connection script in this project.

Safe to re-run: loads whatever's already cached first, merges the freshly-fetched bars in
(market_data_cache.merge_bars(), dedups by timestamp, new wins), and only then overwrites the
cache file -- never blindly replaces it, never loses previously-cached bars outside the newly
requested window.
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
STATE_PATH = PROJECT_ROOT / "var" / "order_state_historical_data_cache_seed"  # unused, see docstring
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
FETCH_COUNT = 50_000  # matches the depth already confirmed available by the probe script

_logger = get_logger("mt5_mcp_trading.scripts.historical_data_cache_seed")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    already_cached = load_bars(path, SYMBOL, TIMEFRAME)
    _logger.info("Cache before this run: %d bar(s) at %s", len(already_cached), path)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store  # deliberately never used -- read-only script

        market_data = McpMarketDataSource(client)
        _logger.info("Requesting %d bars of %s %s ...", FETCH_COUNT, SYMBOL, TIMEFRAME)
        fetched = await market_data.get_bars(SYMBOL, TIMEFRAME, FETCH_COUNT)
        _logger.info("Fetched %d bar(s): %s -> %s",
                     len(fetched), fetched[0].time.isoformat(), fetched[-1].time.isoformat())

    merged = merge_bars(already_cached, fetched)
    save_bars(path, merged)
    _logger.info("Cache after this run: %d bar(s) at %s (was %d, fetched %d, net new %d)",
                 len(merged), path, len(already_cached), len(fetched),
                 len(merged) - len(already_cached))

    print(f"\n=== Historical data cache seed: {SYMBOL} {TIMEFRAME} ===")
    print(f"cache file: {path}")
    print(f"bars before: {len(already_cached)}")
    print(f"bars fetched this run: {len(fetched)}")
    print(f"bars after (merged): {len(merged)}")
    if merged:
        print(f"range: {merged[0].time.isoformat()} -> {merged[-1].time.isoformat()}")
    print("=====================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
