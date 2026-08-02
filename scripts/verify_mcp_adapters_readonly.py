#!/usr/bin/env python3
"""
Live, read-only verification of McpMarketDataSource/McpAccountReader against the real
metatrader-mcp-server -- the step deferred in docs/MCP_ADAPTER_WIRING_CHECKPOINT.md pending
explicit approval (now given). Mirrors scripts/phase3_readonly_verification.py's methodology
(same wrapper launch, same never-touch-.env discipline) but goes through this project's own
typed adapters instead of a raw ClientSession, so this also proves the adapters' parsing and
the live ToolRegistry gate work end-to-end, not just against stub fixtures.

SAFETY:
- Connects with the default ToolRegistry (trading_enabled=False) -- see
  metatrader_tools.build_metatrader_tool_registry(). Every McpClient.call_tool() call checks
  this registry before doing anything else.
- Only calls McpMarketDataSource/McpAccountReader read methods below. No OrderExecutor is
  constructed or reachable in this script.
- Additionally demonstrates, live, that a TRADING-classified tool is refused *before* any RPC
  is sent to the server -- see _prove_trading_is_blocked().
- login/server are never printed: AccountState.login/server are hardcoded None by
  McpAccountReader (see its module docstring), so there is nothing to redact.
- Never reads, logs, or prints .env or any credential; MT5_DEMO_PASSWORD is resolved entirely
  inside the wrapper subprocess (scripts/run_metatrader_mcp_stdio.py), which itself refuses to
  launch unless MT5_ACCOUNT_KIND=DEMO.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.mcp_adapter.tool_registry import TradingDisabledError
from mt5_mcp_trading.mt5_adapter.mcp_account import MagicFilteringUnavailableError, McpAccountReader
from mt5_mcp_trading.mt5_adapter.mcp_market_data import McpMarketDataSource
from mt5_mcp_trading.mt5_adapter.safety import NotDemoAccountError, require_demo_account

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)
TEST_SYMBOL = "BTCUSD"


async def _prove_trading_is_blocked(client: McpClient) -> None:
    """Calls a TRADING-classified tool through the real, live-connected McpClient and confirms
    ToolRegistry.authorize_call() refuses it before McpClient ever awaits session.call_tool() --
    i.e. no RPC for this call reaches the server at all. See McpClient.call_tool()."""
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

        print("\n=== get_connection_state() ===")
        print(await account.get_connection_state())

        print("\n=== get_account_state() (login/server are always None -- see McpAccountReader docstring) ===")
        state = await account.get_account_state()
        print(state)

        print("\n=== require_demo_account() ===")
        try:
            await require_demo_account(account)
            print("PASSED: account reports trade_mode=DEMO")
        except NotDemoAccountError as exc:
            print(
                f"Refused: {exc}\n"
                "This is EXPECTED if the connected account is genuinely demo -- see gap 3 in "
                "docs/mcp_tool_classification.md: metatrader-mcp-server's account_type field is "
                "confirmed inverted, so a real demo account can legitimately report "
                "trade_mode=REAL here. The actual demo-safety boundary for this project is "
                "MT5_ACCOUNT_KIND=DEMO in scripts/run_metatrader_mcp_stdio.py, not this field."
            )

        print(f"\n=== get_bars({TEST_SYMBOL!r}, 'M1', count=5) ===")
        for bar in await market_data.get_bars(TEST_SYMBOL, "M1", count=5):
            print(bar)

        print(f"\n=== get_tick({TEST_SYMBOL!r}) ===")
        print(await market_data.get_tick(TEST_SYMBOL))

        print(f"\n=== get_symbol_info({TEST_SYMBOL!r}) (locally-added tool -- see "
              f"scripts/metatrader_mcp_extended_server.py) ===")
        print(await market_data.get_symbol_info(TEST_SYMBOL))

        print("\n=== get_positions() ===")
        positions = await account.get_positions()
        print(positions if positions else "(no open positions)")

        print("\n=== get_orders() ===")
        orders = await account.get_orders()
        print(orders if orders else "(no pending orders)")

        print("\n=== get_positions(magic=1) -- expected to raise, server exposes no magic field ===")
        try:
            await account.get_positions(magic=1)
            print("!!! UNEXPECTED: did not raise.")
        except MagicFilteringUnavailableError as exc:
            print(f"Raised as expected: {exc}")

    print("\n=== Done. No trading/order-submitting tool was ever sent over the wire. ===")


if __name__ == "__main__":
    asyncio.run(main())
