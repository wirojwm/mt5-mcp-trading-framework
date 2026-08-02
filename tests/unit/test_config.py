from __future__ import annotations

import pytest

from mt5_mcp_trading.config.settings import ExecutionMode, SettingsError, load_settings


def test_default_mode_is_mock_when_unset() -> None:
    settings = load_settings(env={})
    assert settings.mode is ExecutionMode.MOCK
    assert settings.trading_enabled is False


def test_invalid_mode_raises_settings_error() -> None:
    with pytest.raises(SettingsError):
        load_settings(env={"MT5_MCP_MODE": "LIVE"})


@pytest.mark.parametrize(
    "mode", ["READ_ONLY", "MOCK", "DRY_RUN", "SHADOW"],
)
def test_non_demo_execution_modes_never_enable_trading(mode: str) -> None:
    settings = load_settings(env={"MT5_MCP_MODE": mode})
    assert settings.trading_enabled is False


def test_demo_execution_mode_enables_trading() -> None:
    settings = load_settings(env={"MT5_MCP_MODE": "DEMO_EXECUTION"})
    assert settings.trading_enabled is True


def test_login_parsed_as_int() -> None:
    settings = load_settings(env={"MT5_DEMO_LOGIN": "12345"})
    assert settings.mt5_demo_login == 12345


def test_invalid_login_raises_settings_error() -> None:
    with pytest.raises(SettingsError):
        load_settings(env={"MT5_DEMO_LOGIN": "not-a-number"})


def test_password_never_appears_in_repr_or_str() -> None:
    settings = load_settings(env={"MT5_DEMO_PASSWORD": "super-secret-value"})
    assert settings.mt5_demo_password == "super-secret-value"
    assert "super-secret-value" not in repr(settings)
    assert "super-secret-value" not in str(settings)


def test_mt5_account_kind_defaults_to_none_when_unset() -> None:
    settings = load_settings(env={})
    assert settings.mt5_account_kind is None


def test_mt5_account_kind_read_from_env() -> None:
    settings = load_settings(env={"MT5_ACCOUNT_KIND": "DEMO"})
    assert settings.mt5_account_kind == "DEMO"


def test_mt5_account_kind_not_normalized() -> None:
    # load_settings itself does no validation/normalization -- require_demo_account_kind()
    # is what enforces the exact-match "DEMO" requirement, not this loader.
    settings = load_settings(env={"MT5_ACCOUNT_KIND": "real"})
    assert settings.mt5_account_kind == "real"
