#!/usr/bin/env python3
"""
Grid regime filter, Step 3 for the SECOND candidate (docs/GRID_REGIME_FILTER_CHECKPOINT.md):
out-of-sample validation of max_entry_efficiency_ratio=0.013 (the fine-grained-probe plateau
candidate found after 0.2 was rejected) against the HELD-OUT test window -- never read by either
Step 1 sweep or by Step 2's tests. Same discipline as the first candidate's validation script
(scripts/run_demo_execution_backtest_regime_filter_test_window_validation.py) and Phase 8 Step 6:
run the current default (filter off), the rejected 0.2 candidate, and the new 0.013 candidate
back-to-back against the identical test window, report all three honestly in one table.

Deliberately OFFLINE ONLY, no MCP/MT5 call of any kind -- unlike the 0.2 validation script, this
one does NOT open a demo_execution_session or call get_symbol_info(). It reuses the real BTCUSD
SymbolInfo already fetched live and documented in docs/MCP_ADAPTER_WIRING_CHECKPOINT.md (the
figures under "get_symbol_info('BTCUSD') ->"): digits=2, point=0.01, volume_min=0.01,
volume_max=5.0, volume_step=0.01, stops_level=10, freeze_level=0, filling_modes=('FOK',),
spread=1500. These are static broker symbol constraints (not live price), unchanged since that
fetch and reused identically by every backtest script in Phase 8 and this effort so far -- this
script just skips re-fetching what's already known and on record, per this task's explicit
no-live-call requirement.

Everything else replays entirely offline against the local cache
(var/market_data/BTCUSD_M1.csv). No production default (GridStrategyConfig) is changed by this
script. Does not touch the training window at all.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mt5_mcp_trading.backtest.engine import run_backtest
from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.backtest.market_data_cache import cache_path, load_bars, split_bars
from mt5_mcp_trading.backtest.metrics import expectancy_r, has_minimum_sample, max_drawdown_r
from mt5_mcp_trading.domain.models import SymbolInfo
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "var" / "market_data"

SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
CYCLE_INTERVAL_BARS = 5
TRAIN_FRACTION = 0.8  # must match Step 1's split boundary exactly

# Real BTCUSD symbol constraints, fetched live once and documented in
# docs/MCP_ADAPTER_WIRING_CHECKPOINT.md -- reused here (not re-fetched) so this validation makes
# zero MCP/MT5 calls, per this step's explicit offline-only requirement.
SYMBOL_INFO = SymbolInfo(
    symbol="BTCUSD", digits=2, point=0.01, volume_min=0.01, volume_max=5.0, volume_step=0.01,
    stops_level=10, freeze_level=0, filling_modes=("FOK",), spread=1500,
)

_logger = get_logger("mt5_mcp_trading.scripts.backtest_regime_filter_test_window_validation_0013")


def _row(label: str, trades: list[ClosedTrade]) -> str:
    if not trades:
        return f"{label:>32}: no closed trades"
    wins = sum(1 for t in trades if t.r_multiple > 0)
    return (
        f"{label:>32}: {len(trades):>6} trades, win rate {100 * wins / len(trades):5.1f}%, "
        f"expectancy {expectancy_r(trades):+.3f} R, max drawdown {max_drawdown_r(trades):7.3f} R, "
        f"min sample met: {has_minimum_sample(trades)}"
    )


async def main() -> None:
    configure_logging("INFO")

    path = cache_path(CACHE_DIR, SYMBOL, TIMEFRAME)
    bars = load_bars(path, SYMBOL, TIMEFRAME)
    if not bars:
        raise RuntimeError(f"No cached bars at {path} -- run the cache-seed script first")
    train_bars, test_bars = split_bars(bars, train_fraction=TRAIN_FRACTION)
    del train_bars  # deliberately unused -- this script must never read the training window
    _logger.info("Test window (held out): %d bars (%s -> %s)",
                 len(test_bars), test_bars[0].time, test_bars[-1].time)

    print(f"\n=== Grid regime filter Step 3 validation (candidate 0.013): {SYMBOL} {TIMEFRAME} "
          f"-- TEST window only ===")
    print(f"test: {len(test_bars)} bars ({test_bars[0].time} -> {test_bars[-1].time})\n")

    configs = {
        "filter off (current default)": GridStrategyConfig(),
        "rejected candidate max_er=0.2": GridStrategyConfig(max_entry_efficiency_ratio=0.2),
        "new candidate max_er=0.013": GridStrategyConfig(max_entry_efficiency_ratio=0.013),
    }

    for label, grid_config in configs.items():
        print(f"--- {label} ---")
        _logger.info("running %s against test window ...", label)
        ledger = await run_backtest(
            bars=test_bars, symbol=SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            symbol_info=SYMBOL_INFO, grid_config=grid_config, runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(lot_size_mode="fixed", fixed_lot=0.01),
            caps=ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06),
            grid_magic=GRID_MAGIC, runner_magic=RUNNER_MAGIC,
            cycle_interval_bars=CYCLE_INTERVAL_BARS,
        )
        grid_trades = [t for t in ledger.closed_trades if t.magic == GRID_MAGIC]
        runner_trades = [t for t in ledger.closed_trades if t.magic == RUNNER_MAGIC]
        print(_row("grid", grid_trades))
        print(_row("runner (reference)", runner_trades))
        print()

    print("=====================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
