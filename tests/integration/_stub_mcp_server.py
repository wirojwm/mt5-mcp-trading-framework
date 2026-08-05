#!/usr/bin/env python3
"""
Throwaway stub stdio MCP server used ONLY by test_mcp_client_disconnect.py, to prove what
McpClient actually does when the subprocess on the other end of the pipe is killed mid-call --
never run standalone, never imports MT5/metatrader_client/dotenv, never touches credentials or
.env. Not collected by pytest (no test_ prefix).

Deliberately as small as possible: one tool that sleeps, so a test can start a call, then kill
this process while that call is still pending. Writes its own PID to --pidfile on startup (the
only way the test can identify which OS process to kill -- McpClient/stdio_client() don't expose
the child process object to callers).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("disconnect-test-stub")


@mcp.tool()
async def sleep_forever(seconds: float = 9999.0) -> str:
    """Sleeps for `seconds` (default effectively forever for this test's purposes), then
    returns. A real call is expected to never see the return value -- the test kills this
    process while the call is still pending."""
    await asyncio.sleep(seconds)
    return "woke up"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stub MCP stdio server for disconnect testing")
    parser.add_argument("--pidfile", type=str, required=True)
    args = parser.parse_args()

    Path(args.pidfile).write_text(str(os.getpid()), encoding="utf-8")

    mcp.run(transport="stdio")
