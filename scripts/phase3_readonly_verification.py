#!/usr/bin/env python3
"""
Phase 3 read-only verification against the live metatrader-mcp-server.

Launches the exact same wrapper script Claude Code uses (scripts/run_metatrader_mcp_stdio.py),
so this test is representative of the real registered configuration. Never reads .env itself;
credentials are resolved entirely inside the subprocess, out of this script's reach.

SAFETY: TOOL_ALLOWLIST below is the only thing this script will ever call. Anything not in it
raises before a call is attempted. There are 12 write/trading tools on the live server
(place_market_order, place_pending_order, modify_position, modify_pending_order,
close_position, cancel_pending_order, close_all_positions, close_all_positions_by_symbol,
close_all_profitable_positions, close_all_losing_positions, cancel_all_pending_orders,
cancel_pending_orders_by_symbol) -- none of them appear in TOOL_ALLOWLIST, and none are ever
called by this script.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
PYTHON = Path(sys.executable)

# The only tools this script will ever call. Read-only, by function semantics confirmed
# from source (see docs/mcp_tool_classification.md written alongside this script).
TOOL_ALLOWLIST = {
    "get_account_info",
    "get_all_symbols",
    "get_symbol_price",
    "get_candles_latest",
    "get_all_positions",
}

# Fields we will not print even from a successful, legitimate tool response, because they
# mirror values the user typed into .env (MT5_DEMO_LOGIN / MT5_DEMO_SERVER) verbatim.
ACCOUNT_FIELDS_TO_REDACT = {"login", "server"}


def _redact_account_info(info: dict) -> dict:
    redacted = dict(info)
    for key in list(redacted.keys()):
        if key.lower() in ACCOUNT_FIELDS_TO_REDACT:
            redacted[key] = "<redacted: mirrors .env value>"
    return redacted


async def call_tool(session: ClientSession, name: str, arguments: dict | None = None):
    if name not in TOOL_ALLOWLIST:
        raise RuntimeError(f"Refusing to call {name!r}: not in TOOL_ALLOWLIST")
    result = await session.call_tool(name, arguments or {})
    text_parts = [c.text for c in result.content if hasattr(c, "text")]
    return "\n".join(text_parts)


async def main() -> None:
    params = StdioServerParameters(
        command=str(PYTHON),
        args=[str(WRAPPER)],
    )

    print("=== Connecting via the same wrapper Claude Code uses ===")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            print(f"Initialized. Server: {init_result.serverInfo.name} "
                  f"v{init_result.serverInfo.version}")
            print(f"Protocol version: {init_result.protocolVersion}")

            print("\n=== Live tools/list ===")
            tools_result = await session.list_tools()
            tool_names = sorted(t.name for t in tools_result.tools)
            print(f"Tool count: {len(tool_names)}")
            for t in tools_result.tools:
                print(f"  - {t.name}: {t.description}")

            print("\n=== Account info (get_account_info) — login/server redacted ===")
            raw = await call_tool(session, "get_account_info")
            try:
                info = json.loads(raw)
                print(json.dumps(_redact_account_info(info), indent=2))
            except json.JSONDecodeError:
                print("(non-JSON response, not printing raw in case it contains .env values)")

            print("\n=== Symbols (get_all_symbols) — first 10 of N ===")
            raw = await call_tool(session, "get_all_symbols")
            try:
                symbols = json.loads(raw)
                print(f"Total symbols: {len(symbols)}")
                print(symbols[:10])
            except json.JSONDecodeError:
                print(raw[:500])

            test_symbol = "BTCUSD"
            print(f"\n=== Tick for {test_symbol} (get_symbol_price) ===")
            raw = await call_tool(session, "get_symbol_price", {"symbol_name": test_symbol})
            print(raw)

            print(f"\n=== Latest 5 M1 candles for {test_symbol} (get_candles_latest) ===")
            raw = await call_tool(
                session, "get_candles_latest",
                {"symbol_name": test_symbol, "timeframe": "M1", "count": 5},
            )
            print(raw)

            print("\n=== Current positions (get_all_positions) ===")
            raw = await call_tool(session, "get_all_positions")
            print(raw if raw.strip() else "(no open positions)")

    print("\n=== Done. No write/trading tool was called. ===")


if __name__ == "__main__":
    asyncio.run(main())
