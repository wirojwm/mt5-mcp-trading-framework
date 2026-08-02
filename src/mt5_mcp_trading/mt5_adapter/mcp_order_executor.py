"""
Real OrderExecutor backed by metatrader-mcp-server. LIMIT orders only via `submit()`, plus
`cancel()` and `close_position()`. MARKET orders (needing a mandatory SL/TP follow-up via
`modify_position`, since place_market_order drops SL/TP entirely -- see
docs/mcp_tool_classification.md, Known Issues item 7) are deliberately NOT implemented here --
a separate, later, individually-approved step (Phase 6 plan step 6). Pipeline wiring
(run_grid_cycle/run_runner_cycle) is likewise out of scope here.

close_position(), confirmed by reading `metatrader_client/order/close_position.py` and
`client_order.py` directly: takes only a ticket, no volume parameter anywhere in the stack --
it ALWAYS closes a position's full size. There is no partial-close capability to call even if
we wanted one. It internally looks up the position first (a real, live read), then sends a
DEAL-action order_send() with `"position": ticket` set explicitly in the request -- the same
detail the legacy project's own comments flagged as important for hedging accounts (omitting
it can open a new position instead of closing the existing one; this implementation already
gets it right, confirmed by reading the source, not assumed). Reuses
`metatrader_retcodes.parse_trade_response()` unchanged -- the response is the same
`OrderSendResult`-shaped payload already confirmed live in Step 4 (a positional list).

Every mutating call:
1. `require_demo_account_kind()` -- the reliable hard gate, before anything else.
2. `require_demo_account()` -- informational only (see safety.py's module docstring for why).
3. A FRESH reconciliation against real MT5 state before every `submit()` (never cached from
   construction time) -- refuses new orders unless ExecutionPosture is NORMAL. `cancel()`
   additionally refuses to touch a ticket it can't attribute to a local record when posture is
   MANAGE_ONLY. See state/policy.py.
4. Reads `retcode` out of the raw response via `metatrader_retcodes.parse_trade_response()` --
   never trusts the tool's own `error`/`success` field (Known Issues item 7).
5. Records the true submitted intent (magic/comment/deviation/etc, which MT5 itself will not
   honor) to `StateStore` only after a confirmed-done retcode -- never for a rejected order.
6. Verifies by re-reading real MT5 state and matching by ticket only, never guessing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from mt5_mcp_trading.domain.models import ExecutionResult, OrderPlan
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.monitoring.logging_setup import get_logger
from mt5_mcp_trading.mt5_adapter.interfaces import AccountReader
from mt5_mcp_trading.mt5_adapter.metatrader_retcodes import parse_trade_response
from mt5_mcp_trading.mt5_adapter.safety import NotDemoAccountError, require_demo_account, require_demo_account_kind
from mt5_mcp_trading.state.models import ReconciliationReport
from mt5_mcp_trading.state.policy import ExecutionPosture, determine_posture
from mt5_mcp_trading.state.reconcile import reconcile
from mt5_mcp_trading.state.store import StateLoadError, StateStore
from mt5_mcp_trading.state.strategy_registry import strategy_name_for_magic

_logger = get_logger("mt5_mcp_trading.mt5_adapter.mcp_order_executor")

# Sentinel stored in ExecutionResult.retcode (a non-Optional int) when the trading tool
# returned no `data` at all -- a pre-flight rejection before MT5.order_send() was ever called,
# so there is no real broker retcode to report. Never a genuine MT5 retcode value.
_NO_RETCODE = -1


class ExecutionBlockedError(RuntimeError):
    """Raised when the current ExecutionPosture (state/policy.py) refuses the requested
    action -- MANAGE_ONLY blocking a new submission or an unattributed ticket, or BLOCKED
    refusing everything."""


@dataclass(frozen=True, slots=True)
class _PostureCheck:
    posture: ExecutionPosture
    report: Optional[ReconciliationReport]
    error: Optional[StateLoadError]


class McpOrderExecutor:
    def __init__(
        self,
        client: McpClient,
        account: AccountReader,
        state_store: StateStore,
        mt5_account_kind: Optional[str],
    ) -> None:
        self._client = client
        self._account = account
        self._state_store = state_store
        self._mt5_account_kind = mt5_account_kind

    async def _gate(self) -> None:
        require_demo_account_kind(self._mt5_account_kind)
        try:
            await require_demo_account(self._account)
        except NotDemoAccountError as exc:
            _logger.warning("require_demo_account informational check failed: %s", exc)

    async def _current_posture(self) -> _PostureCheck:
        try:
            local_open = self._state_store.all_open()
        except StateLoadError as exc:
            return _PostureCheck(determine_posture(None, exc), None, exc)

        positions = await self._account.get_positions()
        orders = await self._account.get_orders()
        report = reconcile(local_open, positions, orders)
        return _PostureCheck(determine_posture(report, None), report, None)

    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        await self._gate()

        if order_plan.order_type != "LIMIT":
            raise NotImplementedError(
                f"McpOrderExecutor.submit() only supports LIMIT orders in this step, got "
                f"{order_plan.order_type!r} -- MARKET orders need a mandatory SL/TP "
                f"follow-up via modify_position, not yet implemented (Phase 6 plan step 6)."
            )

        check = await self._current_posture()
        if check.posture is not ExecutionPosture.NORMAL:
            raise ExecutionBlockedError(
                f"Refusing to submit a new order: execution posture is "
                f"{check.posture.value} ({check.report or check.error!r}). See state/policy.py."
            )

        side = order_plan.side  # "BUY"/"SELL" -- matches place_pending_order's `type` directly
        raw = await self._client.call_tool(
            "place_pending_order",
            {
                "symbol": order_plan.symbol, "volume": order_plan.volume, "type": side,
                "price": order_plan.price, "stop_loss": order_plan.sl,
                "take_profit": order_plan.tp,
            },
        )
        response = parse_trade_response(raw)

        if not response.done:
            return ExecutionResult(
                order_plan=order_plan, success=False,
                retcode=response.retcode if response.retcode is not None else _NO_RETCODE,
                broker_comment=response.tool_message, verified=False,
                verification_details=(
                    "not submitted -- rejected before reaching MT5 (no data returned)"
                    if response.retcode is None else
                    f"not submitted -- broker retcode={response.retcode}, did not confirm execution"
                ),
            )

        assert response.raw_data is not None  # response.done implies raw_data was parsed
        ticket = int(response.raw_data["order"])
        deal = response.raw_data.get("deal") or None
        executed_price = response.raw_data.get("price")
        executed_volume = response.raw_data.get("volume")
        submitted_at = datetime.now(timezone.utc)

        self._state_store.record_submission(
            ticket=ticket, strategy=strategy_name_for_magic(order_plan.magic),
            magic=order_plan.magic, comment=order_plan.comment, symbol=order_plan.symbol,
            side=side, order_type=order_plan.order_type, requested_volume=order_plan.volume,
            requested_price=order_plan.price, requested_sl=order_plan.sl,
            requested_tp=order_plan.tp, requested_deviation=order_plan.deviation,
            requested_filling_mode=order_plan.filling_mode, requested_expiry=order_plan.expiry,
            retcode=response.retcode, executed_price=executed_price,
            executed_volume=executed_volume, broker_comment=response.tool_message,
            submitted_at=submitted_at,
        )

        verified, verification_details = await self._verify_present(ticket, order_plan.symbol)

        return ExecutionResult(
            order_plan=order_plan, success=True, retcode=response.retcode, ticket=ticket,
            deal=deal, executed_price=executed_price, executed_volume=executed_volume,
            broker_comment=response.tool_message, verified=verified,
            verification_details=verification_details,
        )

    async def _verify_present(
        self, ticket: int, symbol: str, attempts: int = 3
    ) -> tuple[bool, str]:
        for attempt in range(attempts):
            orders = await self._account.get_orders(symbol=symbol)
            if any(o.ticket == ticket for o in orders):
                return True, (
                    f"ticket={ticket} confirmed present via get_pending_orders_with_magic "
                    f"(attempt {attempt + 1}/{attempts}); MT5-reported magic will read 0 "
                    f"(known upstream bug, see docs/mcp_tool_classification.md item 7) -- "
                    f"intended magic recorded separately in local state"
                )
            if attempt < attempts - 1:
                await asyncio.sleep(0.5)
        return False, (
            f"ticket={ticket} NOT found via get_pending_orders_with_magic after {attempts} "
            f"attempts -- submission retcode reported success, but state cannot confirm it"
        )

    async def cancel(self, ticket: int) -> ExecutionResult:
        await self._gate()

        check = await self._current_posture()
        if check.posture is ExecutionPosture.BLOCKED:
            raise ExecutionBlockedError(
                f"Refusing to cancel: execution posture is BLOCKED ({check.error!r})."
            )
        if check.posture is ExecutionPosture.MANAGE_ONLY:
            report = check.report
            assert report is not None
            if ticket not in report.matched and ticket not in report.local_only:
                raise ExecutionBlockedError(
                    f"Refusing to cancel ticket={ticket}: unattributed (no local record), "
                    f"and execution posture is MANAGE_ONLY. See state/policy.py."
                )

        raw = await self._client.call_tool("cancel_pending_order", {"id": ticket})
        response = parse_trade_response(raw)

        if not response.done:
            return ExecutionResult(
                order_plan=None, success=False,
                retcode=response.retcode if response.retcode is not None else _NO_RETCODE,
                ticket=ticket, broker_comment=response.tool_message, verified=False,
                verification_details=f"cancel not confirmed -- retcode={response.retcode}",
            )

        orders = await self._account.get_orders()
        still_present = any(o.ticket == ticket for o in orders)
        self._state_store.record_cancelled(
            ticket, reason="cancel confirmed via McpOrderExecutor.cancel()",
            closed_at=datetime.now(timezone.utc),
        )

        return ExecutionResult(
            order_plan=None, success=True, retcode=response.retcode, ticket=ticket,
            broker_comment=response.tool_message, verified=not still_present,
            verification_details=(
                f"ticket={ticket} confirmed absent from live orders" if not still_present else
                f"ticket={ticket} still present in live orders after a confirmed-done cancel retcode"
            ),
        )

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        await self._gate()

        check = await self._current_posture()
        if check.posture is ExecutionPosture.BLOCKED:
            raise ExecutionBlockedError(
                f"Refusing to close: execution posture is BLOCKED ({check.error!r})."
            )
        if check.posture is ExecutionPosture.MANAGE_ONLY:
            report = check.report
            assert report is not None
            if ticket not in report.matched and ticket not in report.local_only:
                raise ExecutionBlockedError(
                    f"Refusing to close ticket={ticket}: unattributed (no local record), "
                    f"and execution posture is MANAGE_ONLY. See state/policy.py."
                )

        if volume is not None:
            positions = await self._account.get_positions()
            current = next((p for p in positions if p.ticket == ticket), None)
            if current is None:
                raise ExecutionBlockedError(
                    f"Refusing to close ticket={ticket}: volume={volume} was requested but no "
                    f"matching open position was found to compare it against."
                )
            if abs(current.volume - volume) > 1e-9:
                raise NotImplementedError(
                    f"McpOrderExecutor.close_position() does not support partial closes -- "
                    f"the underlying close_position tool has no volume parameter at all and "
                    f"always closes the full position ({current.volume}). Requested "
                    f"volume={volume} does not match. Pass volume=None to close the whole "
                    f"position, matching what will actually happen."
                )

        raw = await self._client.call_tool("close_position", {"id": ticket})
        response = parse_trade_response(raw)

        if not response.done:
            return ExecutionResult(
                order_plan=None, success=False,
                retcode=response.retcode if response.retcode is not None else _NO_RETCODE,
                ticket=ticket, broker_comment=response.tool_message, verified=False,
                verification_details=f"close not confirmed -- retcode={response.retcode}",
            )

        positions = await self._account.get_positions()
        still_present = any(p.ticket == ticket for p in positions)
        self._state_store.record_closed(
            ticket, reason="close confirmed via McpOrderExecutor.close_position()",
            closed_at=datetime.now(timezone.utc),
        )

        deal = response.raw_data.get("deal") if response.raw_data else None
        executed_price = response.raw_data.get("price") if response.raw_data else None

        return ExecutionResult(
            order_plan=None, success=True, retcode=response.retcode, ticket=ticket, deal=deal,
            executed_price=executed_price, broker_comment=response.tool_message,
            verified=not still_present,
            verification_details=(
                f"ticket={ticket} confirmed absent from live positions" if not still_present else
                f"ticket={ticket} still present in live positions after a confirmed-done close retcode"
            ),
        )
