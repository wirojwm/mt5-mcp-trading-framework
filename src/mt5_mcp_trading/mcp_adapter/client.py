"""
Generic MCP stdio client. The only place in this project (besides tests that deliberately
exercise it) that imports the mcp SDK or spawns an MCP server subprocess -- everything else
only ever sees this class's call_tool()/list_tool_names() surface, never the raw
ClientSession.

Every call_tool() checks ToolRegistry.authorize_call() BEFORE calling anything. This is the
actual enforcement point for "never call an order-placement MCP tool directly" and "MCP
trading tools must remain disabled until explicit approval" -- ToolRegistry has existed since
Phase 2, but nothing ever called it until this module exists to be the one thing standing
between this codebase and a live MCP tool invocation.

Spawns the same wrapper script Claude Code itself uses to connect
(scripts/run_metatrader_mcp_stdio.py) -- credentials are resolved entirely inside that
subprocess, never touched by this class or anything that constructs it.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mt5_mcp_trading.mcp_adapter.tool_registry import ToolRegistry


class McpClient:
    def __init__(self, command: str, args: list[str], tool_registry: ToolRegistry) -> None:
        self._command = command
        self._args = args
        self._registry = tool_registry
        self._stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None

    async def __aenter__(self) -> "McpClient":
        self._stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=self._command, args=self._args)
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._session = None
        self._stack = None

    async def list_tool_names(self) -> list[str]:
        if self._session is None:
            raise RuntimeError("McpClient used outside 'async with'")
        result = await self._session.list_tools()
        return [t.name for t in result.tools]

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> str:
        """
        Returns the tool's text response. Every metatrader-mcp-server tool returns either
        JSON or CSV as a single text content block (confirmed in Phase 3's live verification,
        not assumed).
        """
        if self._session is None:
            raise RuntimeError("McpClient used outside 'async with'")
        self._registry.authorize_call(name)  # raises if unclassified or trading-disabled
        result = await self._session.call_tool(name, arguments or {})
        text_parts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(text_parts)
