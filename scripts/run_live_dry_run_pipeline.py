#!/usr/bin/env python3
"""
Live dry-run: proves order_planning.build_order_plan() runs correctly against real, live
SymbolInfo/Tick/MarketBar/position/order data -- not mock/fabricated values -- by running one
full run_grid_cycle() and run_runner_cycle() pass through McpMarketDataSource AND
McpAccountReader together, with explicit user approval. No order is ever submitted anywhere:
DryRunExecutor never calls MCP or MT5 at all (see execution/dry_run.py), and the default
ToolRegistry (trading_enabled=False) additionally refuses any TRADING-classified tool
regardless.

HISTORY: earlier versions of this script used MockAccountReader for the account side, because
run_grid_cycle/run_runner_cycle's account.get_positions(symbol=symbol, magic=magic)/
get_orders(...) calls couldn't run against a real McpAccountReader -- it raised
MagicFilteringUnavailableError whenever magic was not None, since metatrader-mcp-server
exposed no magic number at all. That gap is now resolved (see mt5_adapter/mcp_account.py's
module docstring and scripts/metatrader_mcp_extended_server.py: two locally-added tools
provide real magic numbers, and McpAccountReader genuinely filters by magic instead of
raising), so this script now uses the real McpAccountReader for a true end-to-end run.

SAFETY:
- Connects with the default ToolRegistry (trading_enabled=False) -- see
  metatrader_tools.build_metatrader_tool_registry().
- market_data and account are both real adapters now; executor is DryRunExecutor, which never
  calls MCP or MT5 at all -- see execution/dry_run.py. No OrderExecutor other than
  DryRunExecutor is ever constructed.
- Additionally demonstrates, live, that a TRADING-classified tool is refused before any RPC is
  sent -- same check as scripts/verify_mcp_adapters_readonly.py.
- require_demo_account() is checked informationally (not a hard gate -- see its print branch):
  metatrader-mcp-server's account_type field is known-inverted (gap 3 in
  docs/mcp_tool_classification.md), so this would otherwise always refuse to run on a
  genuinely demo account. The real demo-safety boundary is MT5_ACCOUNT_KIND=DEMO in
  scripts/run_metatrader_mcp_stdio.py, independent of anything read over MCP -- unaffected by
  this being informational only, and DryRunExecutor makes an order-submission mistake here
  impossible regardless.
- Never reads, logs, or prints .env or any credential.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mt5_mcp_trading.execution.dry_run import DryRunExecutor
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import TradingDisabledError
from mt5_mcp_trading.mt5_adapter.mcp_account import McpAccountReader
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.mt5_adapter.safety import NotDemoAccountError, require_demo_account
from mt5_mcp_trading.pipeline.grid_cycle import run_grid_cycle
from mt5_mcp_trading.pipeline.runner_cycle import run_runner_cycle
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
TEST_SYMBOL = "BTCUSD"
TIMEFRAME = "M1"
BARS_COUNT = 100
GRID_MAGIC = 71101
RUNNER_MAGIC = 72101
CAPS = ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)


async def _prove_trading_is_blocked(client: McpClient) -> None:
    print("\n=== Safety check: TRADING tool refused before any RPC (place_market_order) ===")
    try:
        await client.call_tool(
            "place_market_order", {"symbol_name": TEST_SYMBOL, "volume": 0.01, "type": "BUY"}
        )
        print("!!! UNEXPECTED: place_market_order was NOT refused. This is a serious bug.")
        raise SystemExit(1)
    except TradingDisabledError as exc:
        print(f"Refused as expected, no RPC sent: {exc}")


async def main() -> None:
    registry = build_metatrader_tool_registry()  # trading_enabled=False (default)
    assert registry.trading_enabled is False

    async with McpClient(command=str(PYTHON), args=[str(WRAPPER)], tool_registry=registry) as client:
        print("=== Connected via the same wrapper Claude Code uses ===")
        await _prove_trading_is_blocked(client)

        market_data = McpMarketDataSource(client)
        account = McpAccountReader(client)

        print("\n=== require_demo_account() (informational -- see this module's SAFETY note) ===")
        try:
            await require_demo_account(account)
            print("PASSED: account reports trade_mode=DEMO")
        except NotDemoAccountError as exc:
            print(f"Not treated as a hard failure here: {exc}")

        print(f"\n=== run_grid_cycle({TEST_SYMBOL!r}) against real SymbolInfo/tick/bars/"
              f"positions/orders ===")
        grid_results = await run_grid_cycle(
            market_data=market_data, account=account, executor=DryRunExecutor(),
            symbol=TEST_SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            grid_config=GridStrategyConfig(),
            money_config=MoneyConfig(),  # default: fixed, 0.01 lots
            caps=CAPS,
            magic=GRID_MAGIC,
        )
        if grid_results:
            for r in grid_results:
                print(r)
        else:
            print("(nothing submitted -- rejected by a risk guard, or a LIMIT price "
                  "couldn't be normalized far enough from the market)")

        print(f"\n=== run_runner_cycle({TEST_SYMBOL!r}) against real SymbolInfo/tick/bars/"
              f"positions/orders ===")
        runner_result = await run_runner_cycle(
            market_data=market_data, account=account, executor=DryRunExecutor(),
            symbol=TEST_SYMBOL, timeframe=TIMEFRAME, bars_count=BARS_COUNT,
            runner_config=RunnerStrategyConfig(),
            money_config=MoneyConfig(),  # default: fixed, 0.01 lots
            caps=CAPS,
            magic=RUNNER_MAGIC,
        )
        print(runner_result if runner_result is not None else
              "(FLAT signal or rejected by a risk guard, nothing submitted)")

    print("\n=== Done. No order was ever submitted anywhere -- DryRunExecutor only records. ===")


if __name__ == "__main__":
    asyncio.run(main())
