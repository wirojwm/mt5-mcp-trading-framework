#!/usr/bin/env python3
"""
Stage 3 Part 2 of the MCP disconnect/timeout testing effort (docs/PIPELINE_WIRING_CHECKPOINT.md,
"Step 32"). Stage 1/2 (tests/integration/test_mcp_client_disconnect.py,
test_pipeline_loop_disconnect.py) proved McpClient's transport-layer behavior and the pipeline
loop's failure handling using a throwaway stub server -- no MT5, no credentials. This script is
the first thing in that whole effort to touch the real, demo-connected MCP server.

GOAL: confirm the real server behaves the same way the stub did on a forced mid-call disconnect
(McpError, fast, a plain Exception -- not a CancelledError/BaseExceptionGroup escaping past
`except Exception`), AND confirm no process is left running afterward. That second part is new
and specific to the real server: unlike the stub (a single flat process), the real MCP server is
TWO nested processes -- scripts/run_metatrader_mcp_stdio.py (the wrapper McpClient actually
spawns) launches scripts/metatrader_mcp_extended_server.py (the actual MT5-connected server) as
a CHILD via subprocess.run() with inherited stdio, not a proxy. Killing only the wrapper is not
guaranteed to take the grandchild down with it, so this script tree-kills
(`taskkill /F /T /PID`) and then explicitly re-verifies both processes are gone, with a fallback
direct kill of either PID if the tree-kill somehow missed it.

SAFETY -- read before running:
- READ-ONLY. The only MCP tool this script ever calls is get_account_info (via
  McpAccountReader.get_account_state()). No symbol, no order, no `executor` reference anywhere in
  this file -- `executor`/`state_store` from demo_execution_session() are unpacked but never used.
- Goes through the real demo_execution_session() (the one mandated composition root), so
  require_demo_account_kind() and every other existing gate still apply exactly as for every
  other real script. trading_enabled=True is unavoidable (a property of mode=DEMO_EXECUTION, not
  an independent flag -- see config/settings.py), but poses no incremental risk here since no
  TRADING-classified tool is ever called; ToolRegistry still gates that regardless.
- Deliberately kills a real subprocess mid-run. This is the intended mechanism, not a bug -- the
  whole point is to observe what a real drop does. It never touches MT5 terminal state, an order,
  or a position; it only ever kills the connector process(es) on this machine.
- Identifies "its own" processes by DIFFING a process snapshot taken immediately before
  demo_execution_session() is entered against one taken immediately after -- never by matching
  command-line substrings alone against whatever happens to be running. This guards against
  mistaking an unrelated, pre-existing MCP-server process (e.g. one Claude Code or another tool
  already has open) for the one this script itself spawned. If the diff doesn't produce exactly
  one new wrapper process, this script aborts before killing anything.
- A final `finally` block re-scans for and force-kills anything still matching this run's own
  PIDs, regardless of how the script exits (success, timeout, or an unexpected exception) -- a
  safety net against leaking an orphaned process even if something above doesn't behave as
  expected.
- Never reads, logs, or prints MT5_DEMO_PASSWORD or any other credential value.

This script must be reviewed and explicitly approved to RUN separately from being written --
writing it makes no live call by itself (matches every other script in this project's history).
"""

from __future__ import annotations

import asyncio
import dataclasses
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from mt5_mcp_trading.config.settings import ExecutionMode, load_settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.monitoring.logging_setup import configure_logging, get_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
WRAPPER = PROJECT_ROOT / "scripts" / "run_metatrader_mcp_stdio.py"
EXTENDED_SERVER = PROJECT_ROOT / "scripts" / "metatrader_mcp_extended_server.py"
PYTHON = Path(sys.executable)
# Dedicated, throwaway path: state_store is never read or written by this script (no executor
# call is ever made), kept separate from var/order_state purely so that's obvious on inspection.
STATE_PATH = PROJECT_ROOT / "var" / "order_state_mcp_disconnect_smoke_test"

WRAPPER_MARKER = "run_metatrader_mcp_stdio.py"
SERVER_MARKER = "metatrader_mcp_extended_server.py"

RACE_KILL_DELAY_SECONDS = 0.2
POST_KILL_SETTLE_SECONDS = 1.0
CALL_WAIT_BOUND_SECONDS = 10.0
OVERALL_TIMEOUT_SECONDS = 60.0

_logger = get_logger("mt5_mcp_trading.scripts.mcp_disconnect_smoke_test")


def _find_processes(marker: str) -> dict[int, tuple[str, Optional[int]]]:
    """Returns {pid: (command_line, parent_pid)} for every python.exe process whose command line
    contains `marker`, via Get-CimInstance Win32_Process (the same technique already used and
    documented in docs/PIPELINE_WIRING_CHECKPOINT.md, "Step 15", to diagnose the credential-
    exposure issue). Command lines no longer contain credentials since that fix, so this is safe
    to enumerate. ParentProcessId is included so a candidate extended-server process can be
    confirmed as an actual CHILD of a specific wrapper PID, not just something that happens to
    share the marker substring and started around the same time -- see the real ambiguous-diff
    incident recorded in the checkpoint doc that motivated this."""
    escaped = marker.replace("'", "''")
    ps_script = (
        "$procs = Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*" + escaped + "*' } | "
        "Select-Object ProcessId, ParentProcessId, CommandLine; "
        "ConvertTo-Json -InputObject @($procs) -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    import json
    parsed = json.loads(result.stdout)
    if isinstance(parsed, dict):
        parsed = [parsed]
    return {
        int(p["ProcessId"]): (
            str(p.get("CommandLine") or ""),
            int(p["ParentProcessId"]) if p.get("ParentProcessId") is not None else None,
        )
        for p in parsed
    }


def _tree_kill(pid: int) -> None:
    _logger.info("Tree-killing pid=%d (taskkill /F /T)", pid)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True)


def _hard_kill_single(pid: int) -> None:
    _logger.info("Force-killing single pid=%d (taskkill /F, no /T)", pid)
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True)


def _diff_new_pids(
    before: dict[int, tuple[str, Optional[int]]], after: dict[int, tuple[str, Optional[int]]],
) -> dict[int, tuple[str, Optional[int]]]:
    return {pid: info for pid, info in after.items() if pid not in before}


async def _run(results: dict[str, object]) -> None:
    baseline_wrapper = _find_processes(WRAPPER_MARKER)
    baseline_server = _find_processes(SERVER_MARKER)

    settings = dataclasses.replace(load_settings(), mode=ExecutionMode.DEMO_EXECUTION)
    _logger.info("mode=%s, trading_enabled=%s, mt5_account_kind=%r",
                 settings.mode.value, settings.trading_enabled, settings.mt5_account_kind)

    async with demo_execution_session(
        settings, mcp_command=str(PYTHON), mcp_args=[str(WRAPPER)], state_path=STATE_PATH,
    ) as (client, account, executor, state_store):
        del executor, state_store  # deliberately never used -- read-only script, see docstring

        # Step 1: baseline call -- prove the real connection genuinely works before anything is
        # broken.
        state = await account.get_account_state()
        _logger.info("Baseline call OK: server=%r, trade_mode=%r, balance=%s",
                     state.server, state.trade_mode, state.balance)
        results["baseline_call"] = "ok"

        # Step 2: identify OUR OWN new process(es) by diffing against the pre-connect snapshot --
        # never by matching command-line substrings alone (see docstring: guards against mistaking
        # an unrelated pre-existing MCP-server process for this run's own).
        after_wrapper = _find_processes(WRAPPER_MARKER)
        after_server = _find_processes(SERVER_MARKER)
        new_wrapper = _diff_new_pids(baseline_wrapper, after_wrapper)
        new_server = _diff_new_pids(baseline_server, after_server)
        _logger.info("New wrapper process(es): %s", list(new_wrapper.keys()))
        _logger.info("New extended-server process(es): %s", list(new_server.keys()))
        results["new_wrapper_pids"] = list(new_wrapper.keys())
        results["new_server_pids"] = list(new_server.keys())

        # Deliberately NOT recording anything into results["_new_*_pids_for_cleanup"] before this
        # point: an ambiguous diff (not exactly 1 of either process) means we cannot tell "our
        # own leaked process" apart from something unrelated (e.g. a concurrent connection from
        # this project's own Claude Code integration, which uses this exact same wrapper script --
        # see mcp_adapter/client.py's docstring). Auto-killing an unverified set via a safety-net
        # `finally` block would defeat the whole purpose of aborting here instead of guessing.
        # This is not hypothetical: the first real run of this script hit exactly this case (2 new
        # wrapper + 2 new extended-server processes instead of 1 of each) and correctly aborted
        # without killing anything -- see docs/PIPELINE_WIRING_CHECKPOINT.md.
        if len(new_wrapper) != 1:
            raise RuntimeError(
                f"Expected exactly 1 new wrapper process, found {len(new_wrapper)}: "
                f"{list(new_wrapper.keys())} -- aborting before killing anything. This can "
                f"happen if another McpClient connection (this project's or something else's) "
                f"started/stopped concurrently; re-run once nothing else is connecting."
            )
        wrapper_pid, (wrapper_cmdline, _wrapper_ppid) = next(iter(new_wrapper.items()))
        # Extra check beyond the diff itself: confirm the new process's command line also
        # contains the exact resolved Python executable path we asked demo_execution_session()
        # to launch with. Found empirically while building this script that a marker substring
        # alone is not a safe identifier on its own -- an unrelated process whose command line
        # merely CONTAINS "run_metatrader_mcp_stdio.py" as text (e.g. another script quoting that
        # filename) matched during testing. The diff already rules out anything pre-existing;
        # this rules out an unrelated brand-new process that happens to share the substring too.
        if str(PYTHON) not in wrapper_cmdline:
            raise RuntimeError(
                f"New wrapper process pid={wrapper_pid} command line does not contain the "
                f"expected Python executable path ({PYTHON}) -- refusing to kill an unverified "
                f"process. Command line was: {wrapper_cmdline!r}"
            )
        if len(new_server) != 1:
            raise RuntimeError(
                f"Wrapper process pid={wrapper_pid} confirmed, but expected exactly 1 new "
                f"extended-server process, found {len(new_server)}: {list(new_server.keys())} "
                f"-- aborting before killing anything. Cannot confirm which (if any) is the "
                f"wrapper's own child."
            )
        server_pid, (_server_cmdline, server_ppid) = next(iter(new_server.items()))
        if server_ppid != wrapper_pid:
            raise RuntimeError(
                f"New extended-server process pid={server_pid} has ParentProcessId={server_ppid}, "
                f"not {wrapper_pid} (this run's confirmed wrapper) -- it is not this run's own "
                f"child process. Refusing to kill an unverified process."
            )

        # Both processes are now confirmed as this run's own (exactly 1 of each, wrapper's
        # command line matches the exact Python executable used, server is a direct child of the
        # confirmed wrapper). Record them now, immediately -- not at the end of this function --
        # so that main()'s `finally` safety net has real PIDs to check even if something below
        # this point raises unexpectedly (a genuine gap found in this script's first real run:
        # these were previously only recorded after Step 5 completed, so an earlier abort left
        # the safety net with nothing to act on).
        results["_new_wrapper_pids_for_cleanup"] = [wrapper_pid]
        results["_new_server_pids_for_cleanup"] = [server_pid]

        # Step 3: race a second call against a tree-kill. Best-effort, not guaranteed -- a real
        # account read is typically sub-second, unlike Stage 1's fully controllable stub sleep.
        # If it completes anyway, that's informational (real calls are fast), not a failure; the
        # deterministic assertion is Step 5, after the kill is confirmed complete.
        task = asyncio.create_task(account.get_account_state())
        await asyncio.sleep(RACE_KILL_DELAY_SECONDS)
        raced_before_kill_done = task.done()
        t0 = time.monotonic()
        _tree_kill(wrapper_pid)
        if raced_before_kill_done:
            results["raced_call_outcome"] = "completed before the kill could reach it (timing)"
        else:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=CALL_WAIT_BOUND_SECONDS)
                results["raced_call_outcome"] = "completed despite the kill (timing luck)"
            except BaseException as exc:  # noqa: BLE001 -- capturing intentionally
                elapsed = time.monotonic() - t0
                results["raced_call_outcome"] = f"raised {type(exc).__module__}.{type(exc).__name__} after {elapsed:.2f}s: {exc!r}"
                results["raced_call_is_exception_subclass"] = isinstance(exc, Exception)
                results["raced_call_is_exception_group"] = isinstance(exc, BaseExceptionGroup)

        # Step 4: verify process cleanup. Give the OS a moment to finish reaping, then re-scan.
        await asyncio.sleep(POST_KILL_SETTLE_SECONDS)
        confirmed_pids = {wrapper_pid, server_pid}
        still_present = confirmed_pids & (
            set(_find_processes(WRAPPER_MARKER)) | set(_find_processes(SERVER_MARKER))
        )
        if still_present:
            _logger.warning("Process(es) survived the tree-kill, force-killing individually: %s",
                             list(still_present))
            for pid in still_present:
                _hard_kill_single(pid)
        results["orphans_found_and_force_killed"] = list(still_present)

        # Step 5: the deterministic assertion. The connection is now known-dead -- a call made
        # against it must fail the same clean way Stage 1 found, not hang.
        t1 = time.monotonic()
        try:
            await asyncio.wait_for(account.get_account_state(), timeout=CALL_WAIT_BOUND_SECONDS)
            results["post_kill_call_outcome"] = "UNEXPECTED: succeeded after the process was killed"
        except BaseException as exc:  # noqa: BLE001
            elapsed = time.monotonic() - t1
            results["post_kill_call_outcome"] = f"raised {type(exc).__module__}.{type(exc).__name__} after {elapsed:.2f}s (expected): {exc!r}"
            results["post_kill_call_is_exception_subclass"] = isinstance(exc, Exception)
            results["post_kill_call_is_exception_group"] = isinstance(exc, BaseExceptionGroup)


async def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH, override=False)
    settings_for_logging = load_settings()
    configure_logging(settings_for_logging.log_level)

    results: dict[str, object] = {}
    try:
        await asyncio.wait_for(_run(results), timeout=OVERALL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        _logger.error(
            "Script itself timed out after %.0fs -- likely a hang in session cleanup after the "
            "kill (Stage 1's equivalent stub-based test found no such hang; if this fires, that "
            "no longer holds for the real server and needs investigation before trusting it).",
            OVERALL_TIMEOUT_SECONDS,
        )
        raise
    finally:
        # Final safety net regardless of how the above exited: force-kill anything still
        # matching this run's own PIDs. Re-diffs against a fresh baseline scan isn't possible
        # here (we no longer have a pre-connect snapshot in scope), so this uses the PIDs
        # `_run` recorded before returning/raising, if any were recorded.
        leftover_wrapper = results.get("_new_wrapper_pids_for_cleanup", [])
        leftover_server = results.get("_new_server_pids_for_cleanup", [])
        for pid in (*leftover_wrapper, *leftover_server):  # type: ignore[misc]
            still = {**_find_processes(WRAPPER_MARKER), **_find_processes(SERVER_MARKER)}
            if pid in still:
                _logger.warning("Final safety-net kill of leftover pid=%d", pid)
                _hard_kill_single(pid)

    print("\n=== MCP disconnect smoke test (Stage 3 Part 2) result ===")
    for key, value in results.items():
        if key.startswith("_"):
            continue
        print(f"{key}: {value}")
    print("===========================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
