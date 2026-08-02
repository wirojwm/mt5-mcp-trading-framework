"""
demo_execution_session(): the composition root gating trading_enabled=True. No live MCP call
anywhere in this file -- every test here must raise before McpClient.__aenter__ (subprocess
spawn) is ever reached, proven by monkeypatching it to fail the test if it's called at all.
The success path is exercised live in Phase 6's scripted smoke test, not here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mt5_mcp_trading.config.settings import ExecutionMode, Settings
from mt5_mcp_trading.execution.composition import demo_execution_session
from mt5_mcp_trading.mt5_adapter.safety import NotDemoAccountError


def _settings(**overrides: object) -> Settings:
    data: dict[str, object] = dict(mode=ExecutionMode.DEMO_EXECUTION, mt5_account_kind="DEMO")
    data.update(overrides)
    return Settings(**data)  # type: ignore[arg-type]


async def _enter_and_exit(settings: Settings, tmp_path: Path) -> None:
    async with demo_execution_session(
        settings, mcp_command="never-should-run", mcp_args=[],
        state_path=tmp_path / "order_state.json",
    ):
        pass


@pytest.fixture(autouse=True)
def _fail_if_mcp_client_is_ever_entered(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("McpClient.__aenter__ must never be reached in this file")

    monkeypatch.setattr("mt5_mcp_trading.mcp_adapter.client.McpClient.__aenter__", _fail)


def test_raises_when_mode_is_not_demo_execution(tmp_path: Path) -> None:
    settings = _settings(mode=ExecutionMode.DRY_RUN)
    with pytest.raises(RuntimeError, match="DEMO_EXECUTION"):
        asyncio.run(_enter_and_exit(settings, tmp_path))


@pytest.mark.parametrize(
    "mode", [ExecutionMode.READ_ONLY, ExecutionMode.MOCK, ExecutionMode.DRY_RUN, ExecutionMode.SHADOW],
)
def test_raises_for_every_non_demo_execution_mode(mode: ExecutionMode, tmp_path: Path) -> None:
    settings = _settings(mode=mode)
    with pytest.raises(RuntimeError):
        asyncio.run(_enter_and_exit(settings, tmp_path))


@pytest.mark.parametrize("kind", [None, "", "REAL", "demo", "DEMO "])
def test_raises_when_mt5_account_kind_is_not_exactly_demo(kind: str, tmp_path: Path) -> None:
    settings = _settings(mt5_account_kind=kind)
    with pytest.raises(NotDemoAccountError):
        asyncio.run(_enter_and_exit(settings, tmp_path))


def test_account_kind_gate_runs_before_registry_or_client_construction(tmp_path: Path) -> None:
    # mode is valid but mt5_account_kind is not -- proves the account-kind gate is checked
    # independently, not skipped just because the mode check already passed.
    settings = _settings(mode=ExecutionMode.DEMO_EXECUTION, mt5_account_kind=None)
    with pytest.raises(NotDemoAccountError):
        asyncio.run(_enter_and_exit(settings, tmp_path))
