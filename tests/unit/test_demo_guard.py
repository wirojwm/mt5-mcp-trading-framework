"""
require_demo_account is the last line of defense against ever placing an order against a
non-demo account. Tested here against MockAccountReader only — no real MT5/MCP connection.

Uses asyncio.run() directly rather than pytest-asyncio, to keep the test dependency surface
to pytest alone for Phase 2.
"""

from __future__ import annotations

import asyncio

import pytest

from mt5_mcp_trading.domain.models import AccountState
from mt5_mcp_trading.mocks.mock_account_and_executor import MockAccountReader
from mt5_mcp_trading.mt5_adapter.safety import (
    NotDemoAccountError,
    require_demo_account,
    require_demo_account_kind,
)


def _account(trade_mode: str) -> AccountState:
    server = "ThinkMarkets-Demo" if trade_mode == "DEMO" else "ThinkMarkets-Live"
    return AccountState(
        login=180375, server=server, balance=10000.0, equity=10000.0,
        margin_free=10000.0, trade_mode=trade_mode,  # type: ignore[arg-type]
    )


def test_demo_account_passes() -> None:
    reader = MockAccountReader(account_state=_account("DEMO"))
    asyncio.run(require_demo_account(reader))  # must not raise


@pytest.mark.parametrize("trade_mode", ["REAL", "CONTEST"])
def test_non_demo_account_raises(trade_mode: str) -> None:
    reader = MockAccountReader(account_state=_account(trade_mode))
    with pytest.raises(NotDemoAccountError):
        asyncio.run(require_demo_account(reader))


def test_error_message_does_not_leak_more_than_login_and_server() -> None:
    reader = MockAccountReader(account_state=_account("REAL"))
    with pytest.raises(NotDemoAccountError) as excinfo:
        asyncio.run(require_demo_account(reader))
    message = str(excinfo.value)
    assert "180375" in message
    assert "ThinkMarkets-Live" in message
    assert "REAL" in message


# ---------- require_demo_account_kind (the reliable, env-sourced hard gate) ----------

def test_demo_account_kind_passes() -> None:
    require_demo_account_kind("DEMO")  # must not raise


@pytest.mark.parametrize("value", [None, "", "REAL", "demo", "Demo", " DEMO", "DEMO "])
def test_non_exact_demo_account_kind_raises(value: str | None) -> None:
    with pytest.raises(NotDemoAccountError):
        require_demo_account_kind(value)
