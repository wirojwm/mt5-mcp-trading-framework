#!/usr/bin/env python3
"""
One-off diagnostic (Phase 8 continuation, not a scoped deliverable): re-runs the backtest over
the EXACT date range runner's live trades actually happened in (2026-08-05 -> today), instead of
Step 6's original held-out window (2026-07-22 -> 2026-08-05, which ends exactly where live
trading begins and never overlapped it at all). Two goals:

1. Regime check: does runner (current production config, WITH the SL floor) show a materially
   different backtested result on the actual live-trading-period data than on Step 6's window?
2. A same-window, same-config comparison against live Group B (sl_atr_mult=3.0, WITHOUT the SL
   floor -- the retcode-10016 bug window, 2026-08-05 to 2026-08-11 04:42 UTC, -0.898 R across 32
   real trades, per scripts/run_demo_execution_check_20260813_runner_param_split.py) --
   min_stop_distance_fraction_of_price=0.0 reproduces that exact pre-fix config using today's
   code, so it's an honest same-window/same-config backtest counterpart to Group B, not a guess.

Requires var/market_data/BTCUSD_M1.csv to already cover through today -- run
run_demo_execution_historical_data_cache_seed.py first if it doesn't.

READ-ONLY: one get_symbol_info call, same pattern as
run_demo_execution_backtest_test_window_validation.py. No `executor` reference. No production
default changed by this script -- both RunnerStrategyConfig instances built here are local,
never assigned back to the dataclass default.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_mcp_trading.backtest.engine import run_backtest
from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars
from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r, profit_factor, win_rate
from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.domain.models import SymbolInfo
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
STATE_PATH = PROJECT_ROOT / "var" / "order_state_backtest_symbol_info_probe"  # unused, read-only
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
CYCLE_INTERVAL_BARS = 5  # matches the real bounded loop's 300s cadence against M1 bars

# The actual real-world runner live-trading window this compares against (per
# run_demo_execution_check_20260813_runner_param_split.py's own group boundaries).
WINDOW_START = datetime(2026, 8, 5, tzinfo=timezone.utc)


async def _fetch_symbol_info() -> SymbolInfo:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del account, executor, state_store
        return await McpMarketDataSource(client).get_symbol_info(SYMBOL)


def _row(label: str, trades: list[ClosedTrade]) -> str:
    if not trades:
        return f"{label:>50}: no closed trades"
    wins = sum(1 for t in trades if t.r_multiple > 0)
    return (
        f"{label:>50}: {len(trades):>5} trades, win rate {100 * wins / len(trades):5.1f}%, "
        f"expectancy {expectancy_r(trades):+.3f} R, max drawdown {max_drawdown_r(trades):7.3f} R, "
        f"profit_factor {profit_factor(trades):.3f}, min sample met: {has_minimum_sample(trades)}"
    )


async def main() -> None:
    print("Fetching real SymbolInfo (one read-only live call) ...")
    symbol_info = await _fetch_symbol_info()

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path}")
    print(f"Full cache: {len(bars)} bars, {bars[0].time} -> {bars[-1].time}")

    window_bars = [b for b in bars if b.time >= WINDOW_START]
    if not window_bars:
        raise RuntimeError(f"No cached bars on/after {WINDOW_START} -- re-seed the cache first")
    print(f"Live-trading-window slice: {len(window_bars)} bars, "
          f"{window_bars[0].time} -> {window_bars[-1].time}\n")

    configs = {
        "current production (sl_atr_mult=3.0, WITH 1% floor)": RunnerStrategyConfig(),
        "pre-fix equivalent (sl_atr_mult=3.0, NO price floor -- matches live Group B)":
            RunnerStrategyConfig(min_stop_distance_fraction_of_price=0.0),
    }

    for label, runner_config in configs.items():
        print(f"--- {label} ---")
        ledger = await run_backtest(
            bars=window_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=symbol_info, grid_config=GridStrategyConfig(), runner_config=runner_config,
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        runner_trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
        still_open = [p for p in ledger.open_positions.values() if p.magic == RUNNER_MAGIC]
        print(_row("runner (closed)", runner_trades))
        print(f"{'runner (still open at window end)':>50}: {len(still_open)}")
        print()

    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
