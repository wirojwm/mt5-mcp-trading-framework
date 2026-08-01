"""
Typed settings loader. Values come from environment variables (normally populated from a
git-ignored .env file) — never from a hardcoded literal in this codebase.

ExecutionMode is the single switch that determines whether any trading-capable adapter can
ever be constructed. Only DEMO_EXECUTION mode sets Settings.trading_enabled True. There is
no LIVE mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional


class ExecutionMode(str, Enum):
    READ_ONLY = "READ_ONLY"
    MOCK = "MOCK"
    DRY_RUN = "DRY_RUN"
    SHADOW = "SHADOW"
    DEMO_EXECUTION = "DEMO_EXECUTION"


# The only mode(s) in which an OrderExecutor is allowed to actually submit anything to a
# real MCP trading tool. Everything else is read-only or intercepted before it reaches MCP.
_TRADING_CAPABLE_MODES = frozenset({ExecutionMode.DEMO_EXECUTION})


class SettingsError(ValueError):
    """Raised when environment configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    mode: ExecutionMode = ExecutionMode.MOCK
    mt5_demo_login: Optional[int] = None
    # repr=False: this value must never appear in a log line, print(), or exception message
    # produced by dataclass machinery. Callers that need the raw value still have it as an
    # attribute; they just don't get it "for free" via str(settings)/repr(settings).
    mt5_demo_password: Optional[str] = field(default=None, repr=False)
    mt5_demo_server: Optional[str] = None
    mcp_server_command: Optional[str] = None
    log_level: str = "INFO"

    @property
    def trading_enabled(self) -> bool:
        return self.mode in _TRADING_CAPABLE_MODES


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """
    Load Settings from environment variables (or an explicit mapping, for tests).
    Defaults to the safest mode (MOCK) if MT5_MCP_MODE is unset.
    """
    source: Mapping[str, str] = env if env is not None else os.environ

    mode_raw = source.get("MT5_MCP_MODE", ExecutionMode.MOCK.value)
    try:
        mode = ExecutionMode(mode_raw)
    except ValueError as exc:
        valid = ", ".join(m.value for m in ExecutionMode)
        raise SettingsError(
            f"Invalid MT5_MCP_MODE={mode_raw!r}; expected one of: {valid}"
        ) from exc

    login_raw = source.get("MT5_DEMO_LOGIN")
    if login_raw:
        try:
            login = int(login_raw)
        except ValueError as exc:
            raise SettingsError(f"MT5_DEMO_LOGIN={login_raw!r} is not an integer") from exc
    else:
        login = None

    return Settings(
        mode=mode,
        mt5_demo_login=login,
        mt5_demo_password=source.get("MT5_DEMO_PASSWORD") or None,
        mt5_demo_server=source.get("MT5_DEMO_SERVER") or None,
        mcp_server_command=source.get("MCP_SERVER_COMMAND") or None,
        log_level=source.get("LOG_LEVEL", "INFO"),
    )
