"""
Demo-account safety guards.

Two independent checks, deliberately not merged into one:

`require_demo_account` (MCP-sourced, unreliable -- informational only) must be awaited before
constructing any trading-capable executor, and again immediately before every single
order-submitting call — not just once per session. An account can change between those two
points (a reconnect, a config change, a mistake), and this guard was designed to be the last
line of defense against ever placing an order against a non-demo account. In practice, when
fed by `McpAccountReader`, its `AccountState.trade_mode` is confirmed unreliable: the real
connected demo account has been live-verified (see docs/MCP_ADAPTER_WIRING_CHECKPOINT.md) to
report `trade_mode='REAL'`, because of a confirmed upstream `account_type` inversion bug in
metatrader-mcp-server. Treating this as a hard gate would therefore either always refuse to
trade on the genuinely-demo account in use, or require silently downgrading it to informational
somewhere else every time it's used -- so it stays informational-only here, explicitly, once,
rather than quietly worked around per caller. Never delete or "fix" this function to trust
`trade_mode` more than it deserves; if the upstream bug is ever corrected, that's a decision to
revisit deliberately, not a silent behavior change.

`require_demo_account_kind` (env-sourced, reliable -- the actual hard gate) checks the same
`MT5_ACCOUNT_KIND` value scripts/run_metatrader_mcp_stdio.py already refuses to launch the MCP
server subprocess without. Independent of anything read over MCP, so unaffected by the
inversion bug above -- this is the real safety boundary a trading-capable executor must enforce
before doing anything else, both at construction and before every submit.
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


def require_demo_account_kind(mt5_account_kind: str | None) -> None:
    """The reliable hard gate -- see this module's docstring. Raises unless the value is
    exactly the literal string "DEMO" (case-sensitive: no normalization, no guessing)."""
    if mt5_account_kind != "DEMO":
        raise NotDemoAccountError(
            f"Refusing to proceed: MT5_ACCOUNT_KIND={mt5_account_kind!r} is not exactly "
            f"'DEMO'."
        )
