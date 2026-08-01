"""
Classifies MCP tools as READ_ONLY or TRADING and gates calls accordingly.

This is empty of any real classification today: no MT5 MCP server has been connected to or
inspected yet (that is Phase 3). `ToolRegistry` exists now so the *gating mechanism* — the
thing that actually prevents an unclassified or trading tool from being called — is built and
tested before there is anything real to classify, not bolted on afterward.

`authorize_call` never calls anything itself; it only decides whether a call is allowed. The
actual MCP client (built in Phase 3) is expected to call `authorize_call(tool_name)` before
every tool invocation and let the exception propagate if it raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ToolClass(Enum):
    READ_ONLY = "READ_ONLY"
    TRADING = "TRADING"


class ToolNotClassifiedError(RuntimeError):
    """Raised when a tool has not been explicitly classified READ_ONLY or TRADING."""


class TradingDisabledError(RuntimeError):
    """Raised when a TRADING tool is called while trading_enabled is False."""


@dataclass
class ToolRegistry:
    trading_enabled: bool = False
    _classification: dict[str, ToolClass] = field(default_factory=dict)

    def classify(self, tool_name: str, tool_class: ToolClass) -> None:
        self._classification[tool_name] = tool_class

    def classification_of(self, tool_name: str) -> ToolClass | None:
        return self._classification.get(tool_name)

    def authorize_call(self, tool_name: str) -> None:
        """Raise if `tool_name` may not be called right now. Never calls it."""
        tool_class = self.classification_of(tool_name)
        if tool_class is None:
            raise ToolNotClassifiedError(
                f"Tool {tool_name!r} has not been classified as READ_ONLY or TRADING; "
                f"refusing to call an unclassified tool."
            )
        if tool_class is ToolClass.TRADING and not self.trading_enabled:
            raise TradingDisabledError(
                f"Tool {tool_name!r} is a TRADING tool but trading_enabled=False; "
                f"refusing to call it."
            )
