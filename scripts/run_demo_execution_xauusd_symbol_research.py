#!/usr/bin/env python3
"""
Live Pilot Preparation framework's EURUSD-vs-XAUUSD decision criteria (2 and 3)
(docs/LIVE_PILOT_PREPARATION_CHECKPOINT.md), first live-data action toward that decision: read
XAUUSD's real broker specifications and volatility character, and BTCUSD's for direct comparison
(this project's only live-validated symbol, the regime every existing parameter was tuned
against).

READ-ONLY: get_symbols (locate the real broker symbol name -- ThinkMarkets-Demo may not expose
literally "XAUUSD"; returns newline-separated plain text, not JSON, confirmed live -- unlike
get_symbol_info below), get_symbol_info (locally-added tool, point/digits/volume_min/max/step/
trade_stops_level/trade_freeze_level/spread), get_candles_latest (M1 -- the live pipeline's own
TIMEFRAME, scripts/run_demo_execution_pipeline_loop.py -- and D1, for a same-session, real ATR
read at two horizons). All READ_ONLY-classified per docs/mcp_tool_classification.md. No
`executor` reference anywhere in this file, no order of any kind.

This is a data-gathering/reporting script only -- it does NOT decide XAUUSD vs EURUSD (criterion 4
requires the rejected symbol be explicitly recorded with a reason, a human decision, not something
this script should pre-empt). It reports the two numbers criteria 2-3 actually need:

- criterion 2: is the real broker minimum stop distance (trade_stops_level * point) small relative
  to this project's own min_stop_distance_fraction_of_price=0.01 floor -- i.e. does that floor
  stay a deliberate design choice (as it is for BTCUSD, see runner.py's own comment) rather than
  colliding with / being dominated by the broker's own hard minimum?
- criterion 3: is XAUUSD's real ATR-to-price ratio (M1 and D1) structurally close to BTCUSD's --
  "high absolute volatility relative to a lot's $ value", not merely a plausible-sounding
  instrument.

Does NOT compute $ risk-per-trade or a minimum-lot exposure figure -- get_symbol_info exposes no
contract_size/tick_value field (confirmed by reading domain/models.py's SymbolInfo), so a reliable
$ conversion isn't available from this tool alone; left for the (separate, later) minimum-lot/
exposure framework step once that's actually being scoped.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.features.atr import atr as compute_atr
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state_xauusd_symbol_research"  # unused, read-only probe

CANDIDATE_GROUPS = ["*XAU*", "*GOLD*"]
COMPARISON_SYMBOL = "BTCUSD"  # this project's only live-validated regime, for direct comparison
ATR_PERIOD = 14  # matches GridStrategyConfig.atr_period / RunnerStrategyConfig.atr_period
MIN_STOP_DISTANCE_FRACTION_OF_PRICE = 0.01  # runner.py / grid.py's live floor, for comparison

_logger = get_logger("mt5_mcp_trading.scripts.xauusd_symbol_research")


async def _symbol_report(client, market_data: McpMarketDataSource, symbol: str) -> dict[str, object]:
    info = await market_data.get_symbol_info(symbol)
    m1_bars = await market_data.get_bars(symbol, "M1", ATR_PERIOD + 50)
    d1_bars = await market_data.get_bars(symbol, "D1", ATR_PERIOD + 50)

    m1_atr = compute_atr(m1_bars, ATR_PERIOD) if len(m1_bars) >= ATR_PERIOD + 1 else 0.0
    d1_atr = compute_atr(d1_bars, ATR_PERIOD) if len(d1_bars) >= ATR_PERIOD + 1 else 0.0
    price = m1_bars[-1].close if m1_bars else 0.0

    broker_min_stop_price_units = info.stops_level * info.point
    design_floor_price_units = price * MIN_STOP_DISTANCE_FRACTION_OF_PRICE

    return {
        "symbol": symbol,
        "point": info.point,
        "digits": info.digits,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "stops_level_points": info.stops_level,
        "freeze_level_points": info.freeze_level,
        "spread_points": info.spread,
        "filling_modes": info.filling_modes,
        "price": price,
        "m1_atr": m1_atr,
        "m1_atr_pct_of_price": (m1_atr / price * 100) if price else None,
        "d1_atr": d1_atr,
        "d1_atr_pct_of_price": (d1_atr / price * 100) if price else None,
        "broker_min_stop_price_units": broker_min_stop_price_units,
        "broker_min_stop_pct_of_price": (broker_min_stop_price_units / price * 100) if price else None,
        "design_floor_price_units": design_floor_price_units,
        "design_floor_dominates_broker_min": design_floor_price_units > broker_min_stop_price_units,
    }


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    configure_logging(settings.log_level)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    reports: list[dict[str, object]] = []
    resolved_symbol: str | None = None
    candidates_seen: dict[str, list[str]] = {}

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store  # deliberately never used -- read-only research

        market_data = McpMarketDataSource(client)

        for group in CANDIDATE_GROUPS:
            raw = await client.call_tool("get_symbols", {"group": group})
            # get_symbols returns newline-separated plain text, not JSON (confirmed live --
            # unlike get_symbol_info, which is JSON) -- parse accordingly.
            names = [line.strip() for line in raw.splitlines() if line.strip()]
            candidates_seen[group] = names
            _logger.info("get_symbols(group=%r) -> %r", group, names)
            if resolved_symbol is None:
                if "XAUUSD" in names:
                    resolved_symbol = "XAUUSD"  # prefer the plain spot symbol over variants
                elif names:
                    resolved_symbol = names[0]

        if resolved_symbol is None:
            print("\n=== No XAU/GOLD symbol found on this broker via get_symbols(*XAU*/*GOLD*) ===")
            print(f"    groups searched: {candidates_seen}")
            print("    Cannot proceed with criterion 2/3 research -- no symbol to read.\n")
            return

        _logger.info("Resolved XAU candidate symbol: %r", resolved_symbol)

        for symbol in (resolved_symbol, COMPARISON_SYMBOL):
            _logger.info("Reading get_symbol_info + M1/D1 candles for %s ...", symbol)
            reports.append(await _symbol_report(client, market_data, symbol))

    print(f"\n=== XAUUSD-vs-BTCUSD symbol research (real, live-read, {resolved_symbol!r} resolved "
          f"via get_symbols) ===")
    for r in reports:
        print(f"\n--- {r['symbol']} ---")
        print(f"  price (latest M1 close):        {r['price']:.5f}")
        print(f"  point / digits:                 {r['point']} / {r['digits']}")
        print(f"  volume_min/max/step:             {r['volume_min']} / {r['volume_max']} / "
              f"{r['volume_step']}")
        print(f"  spread (points):                 {r['spread_points']}")
        print(f"  broker trade_stops_level:        {r['stops_level_points']} points "
              f"= {r['broker_min_stop_price_units']:.5f} price units "
              f"({r['broker_min_stop_pct_of_price']:.4f}% of price)" if r['broker_min_stop_pct_of_price'] is not None
              else "  broker trade_stops_level:        n/a")
        print(f"  freeze_level:                    {r['freeze_level_points']} points")
        print(f"  filling_modes:                   {r['filling_modes']}")
        print(f"  M1 ATR({ATR_PERIOD}):                     {r['m1_atr']:.5f} "
              f"({r['m1_atr_pct_of_price']:.4f}% of price)" if r['m1_atr_pct_of_price'] is not None
              else f"  M1 ATR({ATR_PERIOD}):                     insufficient bars")
        print(f"  D1 ATR({ATR_PERIOD}):                     {r['d1_atr']:.5f} "
              f"({r['d1_atr_pct_of_price']:.4f}% of price)" if r['d1_atr_pct_of_price'] is not None
              else f"  D1 ATR({ATR_PERIOD}):                     insufficient bars")
        print(f"  design floor ({MIN_STOP_DISTANCE_FRACTION_OF_PRICE:.0%} of price): "
              f"{r['design_floor_price_units']:.5f} price units")
        print(f"  design floor > broker's real minimum? "
              f"{'YES (floor is the binding constraint, a deliberate design choice)' if r['design_floor_dominates_broker_min'] else 'NO (broker minimum exceeds the design floor -- would need a larger floor)'}")

    print("\n=== Criteria this data feeds (docs/LIVE_PILOT_PREPARATION_CHECKPOINT.md) ===")
    print("  Criterion 2 (broker min-stop compatible with the 1%-of-price floor): see each "
          "symbol's 'design floor > broker's real minimum?' line above.")
    print("  Criterion 3 (ATR-to-price ratio structurally close to BTCUSD's regime): compare "
          "XAUUSD's and BTCUSD's M1/D1 ATR %-of-price lines above directly.")
    print("  Criterion 1 (dedicated Phase-8-equivalent backtest + live-verified session) and "
          "criterion 4 (explicit rejection of the other symbol, recorded) are NOT satisfied by "
          "this read alone -- this script reports data only, it does not decide.")
    print("===================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
