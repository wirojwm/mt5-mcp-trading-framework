#!/usr/bin/env python3
"""
Launches the third-party `metatrader-mcp-server` (stdio transport) with credentials taken
from environment variables / a local .env file — never as literal values in this script, in
any Claude Code MCP configuration, or on any command line visible outside this process.

Why this wrapper exists at all: metatrader-mcp-server's `--login`/`--password`/`--server`
CLI options are required by its Click-based entry point, with no environment-variable
fallback built in (confirmed by reading its source directly). Registering that entry point
straight into an MCP client's config would mean writing real MT5 credentials into a config
file. This wrapper keeps the config file secret-free: it only ever references *this script's
path*, and this script resolves the real values at process-launch time.

Launches scripts/metatrader_mcp_extended_server.py (not the pip-installed
metatrader-mcp-server console script directly) -- that script re-registers the same 25 tools
plus one this project added locally (get_symbol_info; see its own module docstring for why).
Same argv interface, so nothing else about this wrapper's credential handling changed.

Required environment variables (normally supplied via a git-ignored .env file next to this
script's project root — see .env.example):
    MT5_DEMO_LOGIN     - MT5 account login (integer)
    MT5_DEMO_PASSWORD  - MT5 account password
    MT5_DEMO_SERVER    - MT5 broker server name (e.g. "ThinkMarkets-Demo")
    MT5_ACCOUNT_KIND   - must be exactly "DEMO". This is a deliberate, explicit opt-in gate,
                         independent of and in addition to the runtime demo-account check
                         (mt5_adapter.safety.require_demo_account) performed once connected.
                         It exists so that launching this process at all requires a conscious,
                         separate acknowledgement — not just naming a variable MT5_DEMO_*.

Optional:
    MT5_PATH           - absolute path to terminal64.exe. metatrader-mcp-server tries to
                         auto-detect this from a fixed list of "standard" MetaQuotes install
                         paths; white-labeled broker terminals (e.g. a broker-branded
                         "<Broker> MetaTrader 5 Terminal" folder) are not in that list, auto-
                         detection fails, and the underlying MetaTrader5.initialize(path=None)
                         call itself then fails with "Invalid path argument" (error -2) --
                         a real, credential-independent bug in that package. Set this to avoid
                         it. Not a secret; safe to store anywhere.

This script never logs, prints, or otherwise exposes MT5_DEMO_PASSWORD.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
EXTENDED_SERVER = PROJECT_ROOT / "scripts" / "metatrader_mcp_extended_server.py"

REQUIRED_VARS = ("MT5_DEMO_LOGIN", "MT5_DEMO_PASSWORD", "MT5_DEMO_SERVER", "MT5_ACCOUNT_KIND")


def _fail(message: str) -> None:
    # stderr only, never stdout (stdout is the MCP stdio transport channel once launched).
    print(f"[run_metatrader_mcp_stdio] {message}", file=sys.stderr)
    sys.exit(1)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        _fail(
            "python-dotenv is not installed in this interpreter. "
            "Install the project's 'mcp' extra: pip install -e '.[mcp]'"
        )
        return
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH, override=False)


def main() -> None:
    _load_env()

    missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        _fail(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            f"Set them in {ENV_PATH} (copy .env.example to .env and fill in DEMO-only values "
            f"yourself -- this script never reads or writes credential values on your behalf)."
        )

    if os.environ["MT5_ACCOUNT_KIND"] != "DEMO":
        _fail(
            f"MT5_ACCOUNT_KIND={os.environ['MT5_ACCOUNT_KIND']!r} but must be exactly 'DEMO'. "
            f"Refusing to launch a server that could connect to a non-demo account."
        )

    if not EXTENDED_SERVER.exists():
        _fail(f"Could not find {EXTENDED_SERVER}. This project's checkout is incomplete.")

    login = os.environ["MT5_DEMO_LOGIN"]
    password = os.environ["MT5_DEMO_PASSWORD"]
    server = os.environ["MT5_DEMO_SERVER"]
    path = os.environ.get("MT5_PATH")

    argv = [
        sys.executable, str(EXTENDED_SERVER),
        "--login", login,
        "--password", password,
        "--server", server,
        "--transport", "stdio",
    ]
    if path:
        argv += ["--path", path]

    # Note: NOT os.execv here. CPython's os.execv on Windows does not reliably preserve
    # argv entries containing spaces (there is no real exec() on Windows -- CPython
    # emulates it), which silently corrupted the --path argument during testing (a
    # space-containing terminal install path got split into multiple bogus arguments).
    # subprocess.run() uses Windows-correct argument quoting (list2cmdline) instead.
    result = subprocess.run(argv)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
