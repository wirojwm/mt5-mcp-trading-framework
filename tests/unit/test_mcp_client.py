"""
Tests McpClient.call_tool()'s timeout wrapping directly -- no real subprocess/MCP session is
ever spawned. Everything else about McpClient (the actual stdio transport, __aenter__/__aexit__)
is exercised only by real live-connection scripts, never unit-tested, since it requires a real
MCP server subprocess -- this file only targets the timeout logic added on top of it, which is
pure asyncio.wait_for() wrapping and can be tested with a fake session object.

Constructs McpClient normally, then sets its private _session attribute directly (bypassing
__aenter__, which would spawn a real subprocess) -- the only practical way to unit test the
timeout wrapping in isolation, since McpClient has no other seam for injecting a fake session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from mt5_mcp_trading.mcp_adapter.client import DEFAULT_CALL_TIMEOUT_SECONDS, McpCallTimeoutError, McpClient
from mt5_mcp_trading.mcp_adapter.metatrader_tools import build_metatrader_tool_registry


@dataclass
class _FakeContent:
    text: str


@dataclass
class _FakeResult:
    content: list[_FakeContent] = field(default_factory=list)


class _FastSession:
    """Returns immediately -- the non-timeout path."""

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> _FakeResult:
        return _FakeResult(content=[_FakeContent(text="ok")])


class _SlowSession:
    """Never returns within any test-scale timeout -- the timeout path."""

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> _FakeResult:
        await asyncio.sleep(10)
        return _FakeResult(content=[_FakeContent(text="too late")])


def _client(session: object, call_timeout_seconds: float) -> McpClient:
    client = McpClient(
        command="unused", args=[], tool_registry=build_metatrader_tool_registry(),
        call_timeout_seconds=call_timeout_seconds,
    )
    client._session = session  # bypass __aenter__ -- see module docstring
    return client


def test_default_timeout_is_thirty_seconds() -> None:
    assert DEFAULT_CALL_TIMEOUT_SECONDS == 30.0


def test_call_tool_returns_text_when_the_call_completes_in_time() -> None:
    client = _client(_FastSession(), call_timeout_seconds=1.0)
    result = asyncio.run(client.call_tool("get_account_info"))
    assert result == "ok"


def test_call_tool_raises_mcp_call_timeout_error_when_the_call_does_not_return_in_time() -> None:
    client = _client(_SlowSession(), call_timeout_seconds=0.05)
    with pytest.raises(McpCallTimeoutError) as exc_info:
        asyncio.run(client.call_tool("get_account_info"))
    assert exc_info.value.tool_name == "get_account_info"
    assert exc_info.value.timeout_seconds == 0.05


def test_call_tool_raises_before_session_is_ever_touched_when_unauthorized() -> None:
    # authorize_call() must still gate every call exactly as before -- the timeout wrapping
    # must not change or bypass that enforcement.
    from mt5_mcp_trading.mcp_adapter.tool_registry import TradingDisabledError

    client = _client(_SlowSession(), call_timeout_seconds=0.05)  # would time out if ever called
    with pytest.raises(TradingDisabledError):
        asyncio.run(client.call_tool("place_market_order", {"symbol": "BTCUSD"}))


def test_call_tool_raises_runtime_error_when_used_outside_async_with() -> None:
    client = McpClient(command="unused", args=[], tool_registry=build_metatrader_tool_registry())
    with pytest.raises(RuntimeError):
        asyncio.run(client.call_tool("get_account_info"))
