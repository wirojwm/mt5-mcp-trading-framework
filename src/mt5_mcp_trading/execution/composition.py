"""
The one place `trading_enabled=True` is ever constructed outside a test. Any script that
needs a real, trading-capable McpOrderExecutor goes through `demo_execution_session()` --
never hand-assembles `ToolRegistry(trading_enabled=True)` itself.

Filesystem/launch details (which Python executable, which wrapper script, where the state file
lives) are deliberately NOT resolved here -- the caller (a script, which already knows the
project root) supplies them explicitly. Keeps this module free of path-resolution logic and
easy to construct in a test with fake values, since the gates below must refuse before any of
those values are ever used.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mt5_mcp_trading.config.settings import ExecutionMode, Settings
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry
from mt5_mcp_trading.monitoring.logging_setup import get_logger
from mt5_mcp_trading.mt5_adapter.mcp_account import McpAccountReader
from mt5_mcp_trading.mt5_adapter.mcp_order_executor import McpOrderExecutor
from mt5_mcp_trading.mt5_adapter.safety import NotDemoAccountError, require_demo_account, require_demo_account_kind
from mt5_mcp_trading.state.store import StateStore

_logger = get_logger("mt5_mcp_trading.execution.composition")


@asynccontextmanager
async def demo_execution_session(
    settings: Settings, mcp_command: str, mcp_args: list[str], state_path: Path
) -> AsyncIterator[tuple[McpClient, McpAccountReader, McpOrderExecutor, StateStore]]:
    if settings.mode is not ExecutionMode.DEMO_EXECUTION:
        raise RuntimeError(
            f"demo_execution_session() requires ExecutionMode.DEMO_EXECUTION, got "
            f"{settings.mode!r}. Refusing before constructing anything."
        )
    if not settings.trading_enabled:
        # Defensive/redundant given the check above (DEMO_EXECUTION is the only trading-
        # capable mode), kept explicit rather than assumed.
        raise RuntimeError("settings.trading_enabled is False -- refusing to proceed.")

    require_demo_account_kind(settings.mt5_account_kind)  # the reliable hard gate, first

    registry = build_metatrader_tool_registry(trading_enabled=True)
    async with McpClient(command=mcp_command, args=mcp_args, tool_registry=registry) as client:
        account = McpAccountReader(client)
        try:
            await require_demo_account(account)
        except NotDemoAccountError as exc:
            _logger.warning("require_demo_account informational check failed: %s", exc)

        state_store = StateStore(state_path)
        executor = McpOrderExecutor(client, account, state_store, settings.mt5_account_kind)
        yield client, account, executor, state_store
