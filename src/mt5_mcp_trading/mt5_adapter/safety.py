"""
Demo-account safety guard.

`require_demo_account` must be awaited before constructing any trading-capable executor, and
again immediately before every single order-submitting call — not just once per session. An
account can change between those two points (a reconnect, a config change, a mistake), and this
guard is the last line of defense against ever placing an order against a non-demo account
before that has been explicitly, separately approved.
"""

from __future__ import annotations

from mt5_mcp_trading.mt5_adapter.interfaces import AccountReader


class NotDemoAccountError(RuntimeError):
    """Raised when the connected account is not a demo account."""


async def require_demo_account(account_reader: AccountReader) -> None:
    state = await account_reader.get_account_state()
    if state.trade_mode != "DEMO":
        raise NotDemoAccountError(
            f"Refusing to proceed: account trade_mode={state.trade_mode!r} is not DEMO "
            f"(login={state.login}, server={state.server})"
        )
