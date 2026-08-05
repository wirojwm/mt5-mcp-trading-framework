#!/usr/bin/env python3
"""
Phase 8, Step 2's first action (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): discovers how much
real historical bar depth this demo connection can actually retrieve for BTCUSD, across several
timeframes, before any backtest engine or local cache is designed around an assumed depth.

READ-ONLY, same risk category as every other real-connection read this project has made: only
calls get_candles_latest (via McpMarketDataSource.get_bars(), a READ_ONLY-classified tool). No
symbol trading call, no `executor` reference anywhere in this file -- `executor`/`state_store`
from demo_execution_session() are unpacked and immediately discarded, same pattern as
scripts/run_demo_execution_mcp_disconnect_smoke_test.py.

get_candles_latest wraps MetaTrader5.copy_rates_from_pos(symbol, tf, 0, count) directly
(confirmed by reading .venv/Lib/site-packages/metatrader_client/market/get_candles_latest.py) --
there is no hard-coded count cap in this project's or the vendored client's own code; the actual
limit is however much history this specific MT5 terminal has already cached locally. Requesting
more than is available does not error or block on a broker download, it just returns fewer bars
than asked -- so a single large request per timeframe is enough to discover the real ceiling.

This is a discovery probe, not Step 2's full deliverable -- no local cache/persistence is written
here (a separate, later action once the depth found here is known and a storage format is
decided). Prints a summary table only.
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
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state_historical_data_probe"  # unused, see docstring

SYMBOL = "BTCUSD"
TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"]
PROBE_COUNT = 50_000  # deliberately far more than any timeframe is expected to have cached

_logger = get_logger("mt5_mcp_trading.scripts.historical_data_probe")


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    results: list[dict[str, object]] = []

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store  # deliberately never used -- read-only probe

        market_data = McpMarketDataSource(client)

        for timeframe in TIMEFRAMES:
            _logger.info("Requesting %d bars of %s %s ...", PROBE_COUNT, SYMBOL, timeframe)
            try:
                bars = await market_data.get_bars(SYMBOL, timeframe, PROBE_COUNT)
            except Exception as exc:  # noqa: BLE001 -- report per-timeframe, don't abort the probe
                _logger.warning("  %s failed: %r", timeframe, exc)
                results.append({"timeframe": timeframe, "error": repr(exc)})
                continue

            if not bars:
                results.append({"timeframe": timeframe, "bar_count": 0})
                continue

            span = bars[-1].time - bars[0].time
            results.append({
                "timeframe": timeframe,
                "bar_count": len(bars),
                "earliest": bars[0].time.isoformat(),
                "latest": bars[-1].time.isoformat(),
                "span_days": round(span.total_seconds() / 86400, 1),
            })
            _logger.info("  got %d bars, %s -> %s (%.1f days)",
                         len(bars), bars[0].time.isoformat(), bars[-1].time.isoformat(),
                         span.total_seconds() / 86400)

    print(f"\n=== Historical data depth probe: {SYMBOL} ===")
    for row in results:
        if "error" in row:
            print(f"{row['timeframe']:>4}: FAILED -- {row['error']}")
        elif row["bar_count"] == 0:
            print(f"{row['timeframe']:>4}: 0 bars returned")
        else:
            print(f"{row['timeframe']:>4}: {row['bar_count']:>6} bars, "
                  f"{row['earliest']} -> {row['latest']} ({row['span_days']} days)")
    print("===========================================\n")


if __name__ == "__main__":
    asyncio.run(main())
