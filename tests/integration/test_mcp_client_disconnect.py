"""
Phase 7's never-done slice, closed here: what does McpClient actually do when the process on
the other end of its stdio pipe dies mid-call? Every prior "a dropped connection is fatal, no
reconnect logic" claim (docs/PIPELINE_WIRING_CHECKPOINT.md, "Step 14" decision 4) was inferred
from six live loop runs that never happened to drop -- that proves nothing about what a real
drop actually does. This file forces one, for real, and observes the result directly.

Uses a throwaway stub stdio MCP server (_stub_mcp_server.py, this directory) instead of the
real metatrader-mcp-server: no MT5 import, no dotenv, no credentials, no .env, no network
beyond a local stdio pipe to a child Python process this test itself spawns and kills. This is
McpClient's own transport-layer behavior under test, not anything strategy/order-related -- the
real subprocess is the actual mechanism ("kill it, see what happens"), the trading stack around
it is intentionally absent.

Findings from the first real run (recorded here, not just claimed): the installed mcp SDK's
ClientSession itself detects the closed pipe and raises mcp.shared.exceptions.McpError
("Connection closed") in well under a second -- not McpClient's own 30s asyncio.wait_for
timeout firing, and not a raw asyncio.CancelledError/BaseExceptionGroup escaping past
`except Exception` (a real risk with anyio task-group-based transports, seriously considered
before running this, and worth locking in with an explicit assertion rather than trusting it
holds forever as the mcp SDK version changes).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from mt5_mcp_trading.mcp_adapter.client import DEFAULT_CALL_TIMEOUT_SECONDS, McpClient
from mt5_mcp_trading.mcp_adapter.tool_registry import ToolClass, ToolRegistry

STUB_SERVER = Path(__file__).resolve().parent / "_stub_mcp_server.py"

# Generous vs. the sub-second failure actually observed -- this bounds the test's own worst
# case, not a claim about how long a real disconnect should take to surface.
DISCONNECT_DETECTION_BOUND_SECONDS = 10.0


def _kill_hard(pid: int) -> None:
    """Unconditional, non-graceful process kill -- simulates a crash/network-level drop, not a
    clean shutdown. On Windows, os.kill() has no real signal delivery; any value (including
    SIGTERM) calls TerminateProcess() directly, which is exactly the hard-kill this test wants.
    On POSIX, SIGKILL is used explicitly for the same reason (SIGTERM could be caught)."""
    if sys.platform == "win32":
        os.kill(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGKILL)


def _stub_client(pidfile: Path) -> McpClient:
    registry = ToolRegistry(trading_enabled=False)
    registry.classify("sleep_forever", ToolClass.READ_ONLY)
    return McpClient(
        command=sys.executable, args=[str(STUB_SERVER), "--pidfile", str(pidfile)],
        tool_registry=registry, call_timeout_seconds=DEFAULT_CALL_TIMEOUT_SECONDS,
    )


async def _read_stub_pid(pidfile: Path) -> int:
    for _ in range(100):  # up to ~10s for the stub to start and write its own pid
        if pidfile.exists():
            content = pidfile.read_text(encoding="utf-8").strip()
            if content:
                return int(content)
        await asyncio.sleep(0.1)
    raise TimeoutError(f"stub server never wrote {pidfile}")


async def _disconnect_mid_call(tmp_path: Path) -> tuple[BaseException, float, McpClient]:
    """Connects to the stub, starts a call that will never return on its own, kills the stub
    process while that call is still pending, and returns (raised_exception, elapsed_seconds,
    client) with the client still inside its `async with` block (caller must exit it)."""
    pidfile = tmp_path / "stub.pid"
    client = _stub_client(pidfile)
    await client.__aenter__()

    pid = await _read_stub_pid(pidfile)
    task = asyncio.create_task(client.call_tool("sleep_forever"))
    await asyncio.sleep(0.3)  # let the call actually reach the subprocess before killing it
    assert not task.done(), "call completed before the kill -- test setup is racing itself"

    t0 = time.monotonic()
    _kill_hard(pid)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=DISCONNECT_DETECTION_BOUND_SECONDS)
        raise AssertionError("call_tool() returned normally after its subprocess was killed")
    except BaseException as exc:  # noqa: BLE001 -- capturing intentionally, incl. non-Exception
        elapsed = time.monotonic() - t0
        return exc, elapsed, client


def test_mid_call_disconnect_raises_promptly_not_via_the_30s_timeout(tmp_path: Path) -> None:
    async def scenario() -> None:
        exc, elapsed, client = await _disconnect_mid_call(tmp_path)
        try:
            assert elapsed < DISCONNECT_DETECTION_BOUND_SECONDS, (
                f"took {elapsed:.2f}s to surface -- looks like it fell through to the 30s "
                f"call-level timeout rather than detecting the closed pipe directly"
            )
            # The actual, most important assertion this whole test exists for: a real disconnect
            # must never be invisible to `except Exception` (grid_cycle.py/runner_cycle.py/
            # run_demo_execution_pipeline_loop.py all use exactly that blanket catch). A raw
            # asyncio.CancelledError or a bare BaseExceptionGroup would silently defeat every one
            # of them.
            assert isinstance(exc, Exception), (
                f"disconnect surfaced as {type(exc)!r}, which is NOT an Exception subclass -- "
                f"every caller in this codebase that catches disconnects uses `except Exception`, "
                f"so this would propagate uncaught past every one of them"
            )
            assert not isinstance(exc, BaseExceptionGroup), (
                f"disconnect surfaced as an exception group ({exc!r}) -- callers using "
                f"`except Exception` would need explicit ExceptionGroup handling to catch this, "
                f"which none of them currently have"
            )
        finally:
            await client.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_client_cleanup_after_a_disconnect_does_not_hang(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, _, client = await _disconnect_mid_call(tmp_path)
        t0 = time.monotonic()
        await asyncio.wait_for(client.__aexit__(None, None, None), timeout=10.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 10.0, f"__aexit__ took {elapsed:.2f}s -- cleanup after a dead child hung"

    asyncio.run(scenario())


def test_a_second_call_after_disconnect_also_fails_fast_not_hangs(tmp_path: Path) -> None:
    """Loop code (and McpOrderExecutor's own _current_posture() re-check) will keep trying to
    use the session after a drop, once per subsequent action, until something notices and stops
    the whole run. Every one of those later attempts must fail fast too, not hang -- otherwise a
    single drop could still wedge a long-running process even though the FIRST failure was
    handled correctly."""
    async def scenario() -> None:
        exc1, _, client = await _disconnect_mid_call(tmp_path)
        try:
            t0 = time.monotonic()
            with pytest.raises(Exception) as exc_info:
                await asyncio.wait_for(client.call_tool("sleep_forever"), timeout=10.0)
            elapsed = time.monotonic() - t0
            assert elapsed < 10.0, f"second call took {elapsed:.2f}s -- did not fail fast"
            assert not isinstance(exc_info.value, BaseExceptionGroup)
        finally:
            await client.__aexit__(None, None, None)

    asyncio.run(scenario())
