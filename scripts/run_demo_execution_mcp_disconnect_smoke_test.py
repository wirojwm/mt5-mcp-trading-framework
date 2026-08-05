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
  already has open) for the one this script itself spawned. The diff must form exactly ONE
  connected process tree (a single root with no parent inside the new-process set, everything
  else in the set reachable from it) or this script aborts before killing anything -- NOT exactly
  one PID per marker, because this machine's venv launcher (.venv/Scripts/python.exe, a ~235KB
  CPython venv stub per pyvenv.cfg's home=miniconda3) spawns the base interpreter as a genuine
  child OS process rather than exec'ing in place, so a single logical wrapper+extended-server
  connection legitimately shows up as up to 4 real PIDs, confirmed directly (not guessed) after
  this script's first two real runs both hit that shape. A genuinely unrelated second connection
  would show up as a SECOND, disconnected root instead, which still correctly aborts.
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


def _format_procs(procs: dict[int, tuple[str, Optional[int]]]) -> str:
    """Full diagnostic detail (pid, ppid, complete command line -- never truncated) for logging
    and for embedding directly in an abort's exception message, so a single glance at either the
    log stream or the traceback shows exactly what was found, not just PIDs."""
    if not procs:
        return " (none)"
    lines = [f"  pid={pid} ppid={ppid} cmdline={cmdline!r}" for pid, (cmdline, ppid) in procs.items()]
    return "\n" + "\n".join(lines)


async def _run(results: dict[str, object]) -> None:
    baseline_wrapper = _find_processes(WRAPPER_MARKER)
    baseline_server = _find_processes(SERVER_MARKER)
    _logger.info("Baseline wrapper process(es) (pre-existing, excluded from the diff):%s",
                 _format_procs(baseline_wrapper))
    _logger.info("Baseline extended-server process(es) (pre-existing, excluded from the diff):%s",
                 _format_procs(baseline_server))

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
        # Full detail, unconditionally, every run -- not just on the ambiguous-diff path below.
        # Added after the first two real runs both hit that path and only PIDs were logged,
        # leaving no way to tell what the "extra" process actually was without a follow-up step.
        _logger.info("New wrapper process(es):%s", _format_procs(new_wrapper))
        _logger.info("New extended-server process(es):%s", _format_procs(new_server))
        results["new_wrapper_pids"] = list(new_wrapper.keys())
        results["new_server_pids"] = list(new_server.keys())

        # This machine's venv launcher is a real, confirmed structural quirk, not a coincidence:
        # .venv/Scripts/python.exe is a ~235KB CPython venv stub (pyvenv.cfg: home=miniconda3)
        # that spawns the base miniconda interpreter as a genuine CHILD OS process rather than
        # exec'ing in place -- confirmed directly (file size + pyvenv.cfg contents), not guessed,
        # after the first two real runs of this script both found exactly 2 processes matching
        # each marker instead of 1. The full command-line diagnostic added for exactly this
        # showed the "extra" process at each level is the SAME script/args, parented by the
        # "expected" one -- e.g. wrapper stub (pid A, .venv python) -> real wrapper (pid B,
        # miniconda python, ppid=A) -> extended-server stub (pid C, ppid=B) -> real extended-
        # server (pid D, ppid=C). Not two independent connections; one logical spawn chain,
        # doubled at every level by the venv redirect. So instead of requiring exactly 1 PID per
        # marker, this validates the whole new-process set forms exactly ONE connected tree (one
        # root with no parent inside the set, everything else in the set reachable from it) --
        # true regardless of how many re-exec layers this specific machine's interpreter
        # introduces, but still refuses to guess if there are genuinely two unrelated trees (e.g.
        # an actual concurrent connection from elsewhere, which is what the original exactly-1
        # check was trying, incorrectly, to detect this same way).
        new_procs = {**new_wrapper, **new_server}
        roots = [pid for pid, (_cmd, ppid) in new_procs.items() if ppid not in new_procs]
        if len(roots) != 1:
            raise RuntimeError(
                f"Expected exactly 1 root process among all new wrapper/extended-server "
                f"processes, found {len(roots)}: {roots} -- aborting before killing anything. "
                f"This means the new processes do not form a single connected spawn tree (e.g. a "
                f"genuine second, unrelated McpClient connection started concurrently), not just "
                f"this machine's known venv-stub double-process quirk. "
                f"Full detail:{_format_procs(new_procs)}"
            )
        root_pid = roots[0]
        if root_pid not in new_wrapper:
            raise RuntimeError(
                f"The single root process (pid={root_pid}) is not a wrapper-marker match -- "
                f"expected the root of the spawn tree to be run_metatrader_mcp_stdio.py. "
                f"Aborting before killing anything. Full detail:{_format_procs(new_procs)}"
            )
        root_cmdline = new_procs[root_pid][0]
        # Extra check beyond the diff itself: confirm the root's command line also contains the
        # exact resolved Python executable path we asked demo_execution_session() to launch with.
        # Found empirically while building this script that a marker substring alone is not a
        # safe identifier on its own -- an unrelated process whose command line merely CONTAINS
        # "run_metatrader_mcp_stdio.py" as text (e.g. another script quoting that filename)
        # matched during testing. The diff already rules out anything pre-existing; this rules
        # out an unrelated brand-new root process that happens to share the substring too.
        if str(PYTHON) not in root_cmdline:
            raise RuntimeError(
                f"Root wrapper process pid={root_pid} command line does not contain the expected "
                f"Python executable path ({PYTHON}) -- refusing to kill an unverified process. "
                f"Command line was: {root_cmdline!r}"
            )
        if not new_server:
            raise RuntimeError(
                f"Root wrapper process pid={root_pid} confirmed, but no new extended-server "
                f"process was found at all -- aborting before killing anything. "
                f"Full detail:{_format_procs(new_procs)}"
            )

        # The whole tree is now confirmed as this run's own (single root, root is a verified
        # wrapper process, at least one extended-server descendant exists). Record every PID in
        # the tree now, immediately -- not at the end of this function -- so that main()'s
        # `finally` safety net has real PIDs to check even if something below this point raises
        # unexpectedly (a genuine gap found in this script's first real run: these were
        # previously only recorded after Step 5 completed, so an earlier abort left the safety
        # net with nothing to act on).
        all_tree_pids = list(new_procs.keys())
        results["_new_tree_pids_for_cleanup"] = all_tree_pids

        # Step 3: race a second call against a tree-kill. Best-effort, not guaranteed -- a real
        # account read is typically sub-second, unlike Stage 1's fully controllable stub sleep.
        # If it completes anyway, that's informational (real calls are fast), not a failure; the
        # deterministic assertion is Step 5, after the kill is confirmed complete. Killing the
        # single root with /T recursively takes down the whole descendant chain regardless of how
        # many venv-stub re-exec layers exist beneath it.
        task = asyncio.create_task(account.get_account_state())
        await asyncio.sleep(RACE_KILL_DELAY_SECONDS)
        raced_before_kill_done = task.done()
        t0 = time.monotonic()
        _tree_kill(root_pid)
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
        confirmed_pids = set(all_tree_pids)
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
        # `_run` recorded before returning/raising, if any were recorded -- the whole confirmed
        # process tree (root wrapper + every descendant, however many venv-stub re-exec layers
        # this machine happens to introduce), not just a single wrapper/server pair.
        leftover_tree = results.get("_new_tree_pids_for_cleanup", [])
        for pid in leftover_tree:  # type: ignore[misc]
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
