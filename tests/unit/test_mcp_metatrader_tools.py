from __future__ import annotations

from mt5_mcp_trading.mcp_adapter.metatrader_tools import (
    READ_ONLY_TOOLS,
    TRADING_TOOLS,
    build_metatrader_tool_registry,
)
from mt5_mcp_trading.mcp_adapter.tool_registry import (
    ToolClass,
    TradingDisabledError,
)


def test_all_26_tools_are_classified_and_none_overlap() -> None:
    # 25 upstream (13 read-only, 12 trading) + 1 locally-registered read-only tool
    # (get_symbol_info -- see metatrader_tools.py's module docstring).
    assert len(READ_ONLY_TOOLS) == 14
    assert len(TRADING_TOOLS) == 12
    assert set(READ_ONLY_TOOLS).isdisjoint(TRADING_TOOLS)


def test_defaults_to_trading_disabled() -> None:
    registry = build_metatrader_tool_registry()
    assert registry.trading_enabled is False
    registry.authorize_call("get_account_info")  # read-only: fine regardless
    for name in TRADING_TOOLS:
        try:
            registry.authorize_call(name)
            assert False, f"{name} should have been refused with trading disabled"
        except TradingDisabledError:
            pass


def test_explicit_trading_enabled_allows_trading_tools() -> None:
    registry = build_metatrader_tool_registry(trading_enabled=True)
    for name in TRADING_TOOLS:
        registry.authorize_call(name)  # must not raise


def test_every_read_only_tool_classified_correctly() -> None:
    registry = build_metatrader_tool_registry()
    for name in READ_ONLY_TOOLS:
        assert registry.classification_of(name) == ToolClass.READ_ONLY


def test_every_trading_tool_classified_correctly() -> None:
    registry = build_metatrader_tool_registry()
    for name in TRADING_TOOLS:
        assert registry.classification_of(name) == ToolClass.TRADING


def test_unknown_tool_name_stays_unclassified() -> None:
    registry = build_metatrader_tool_registry()
    assert registry.classification_of("get_terminal_info") is None  # confirmed not to exist
