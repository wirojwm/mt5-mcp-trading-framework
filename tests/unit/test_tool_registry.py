from __future__ import annotations

import pytest

from mt5_mcp_trading.mcp_adapter.tool_registry import (
    ToolClass,
    ToolNotClassifiedError,
    ToolRegistry,
    TradingDisabledError,
)


def test_unclassified_tool_is_refused() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotClassifiedError):
        registry.authorize_call("mt5_place_order")


def test_read_only_tool_allowed_regardless_of_trading_enabled() -> None:
    registry = ToolRegistry(trading_enabled=False)
    registry.classify("mt5_get_symbol_tick", ToolClass.READ_ONLY)
    registry.authorize_call("mt5_get_symbol_tick")  # must not raise


def test_trading_tool_refused_when_trading_disabled() -> None:
    registry = ToolRegistry(trading_enabled=False)
    registry.classify("mt5_place_order", ToolClass.TRADING)
    with pytest.raises(TradingDisabledError):
        registry.authorize_call("mt5_place_order")


def test_trading_tool_allowed_when_trading_enabled() -> None:
    registry = ToolRegistry(trading_enabled=True)
    registry.classify("mt5_place_order", ToolClass.TRADING)
    registry.authorize_call("mt5_place_order")  # must not raise


def test_classification_of_returns_none_for_unknown_tool() -> None:
    registry = ToolRegistry()
    assert registry.classification_of("nonexistent_tool") is None
