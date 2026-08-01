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

This script never logs, prints, or otherwise exposes MT5_DEMO_PASSWORD.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

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


def _find_console_script() -> Path:
    """Locate metatrader-mcp-server(.exe) next to the interpreter running this wrapper,
    i.e. in the same venv, rather than trusting whatever happens to be first on PATH."""
    scripts_dir = Path(sys.executable).resolve().parent
    candidates = [scripts_dir / "metatrader-mcp-server.exe", scripts_dir / "metatrader-mcp-server"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    _fail(
        f"Could not find metatrader-mcp-server next to {sys.executable!r}. "
        f"Install it into this venv: pip install -e '.[mcp]'"
    )
    raise SystemExit(1)  # unreachable, _fail already exits; keeps type-checkers happy


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

    server_path = _find_console_script()

    login = os.environ["MT5_DEMO_LOGIN"]
    password = os.environ["MT5_DEMO_PASSWORD"]
    server = os.environ["MT5_DEMO_SERVER"]

    argv = [
        str(server_path),
        "--login", login,
        "--password", password,
        "--server", server,
        "--transport", "stdio",
    ]

    # Replace this process entirely -- no wrapper process left running alongside it, and
    # no risk of this script's own stdout ever interleaving with the MCP stdio stream.
    os.execv(str(server_path), argv)


if __name__ == "__main__":
    main()
