"""
Real OrderExecutor backed by metatrader-mcp-server. LIMIT and MARKET orders via `submit()`,
plus `cancel()` and `close_position()`. Pipeline wiring (run_grid_cycle/run_runner_cycle) is
out of scope here, as is any live call -- see docs/PHASE6_CONTROLLED_DEMO_EXECUTION_CHECKPOINT.md
for what has and hasn't been live-proven.

MARKET orders (Phase 6 Step 6), confirmed by reading `metatrader_client/client_order.py` and
`metatrader_mcp/server.py` directly, not assumed: the `place_market_order` MCP tool accepts
only `symbol`/`volume`/`type` -- no `sl`/`tp`/`magic`/`comment`/`deviation` parameter exists at
that layer at all, even though a function one level further down in the third-party package
does accept and forward stop_loss/take_profit (it's just never exposed up to the tool this
project can actually call). Every MARKET order therefore opens completely naked at placement.
The only way to attach SL/TP is a mandatory follow-up `modify_position(id, stop_loss,
take_profit)` call -- confirmed to reuse the exact same `OrderSendResult`-shaped response
already handled by `metatrader_retcodes.parse_trade_response()` (an SLTP-action order_send()
internally), so no new parsing code was needed for it.

This creates a real window, between the position confirming open and SL/TP confirming
attached, where a genuine, unattributed-risk, unprotected position exists on the account.
`_submit_market()` handles it deliberately conservatively:
- Local state is written as `status="OPEN_UNPROTECTED"` immediately after the position
  confirms open -- BEFORE the modify_position attempt -- so a crash between the two calls
  still leaves an accurate record, never a silently-lost one.
- Exactly one `modify_position` attempt is made. No retry, ever, regardless of outcome.
- Success is never taken on retcode alone: a fresh live read of the position's actual sl/tp
  must also agree, or attachment is treated as failed even if the retcode said done.
- On any failure to confirm attachment, `SlTpAttachmentFailedError` is raised and the ticket
  stays `OPEN_UNPROTECTED`. No automatic retry and no automatic close is ever attempted --
  auto-closing on failure would itself be a second unattended live trading decision, exactly
  the class of behavior this project has consistently refused to do (see Step 5's MANAGE_ONLY
  refusal, which stopped and reported rather than acting). Recovery is a separate,
  explicitly-approved call (typically `close_position()` on that one ticket) made by a human,
  not by this code.
- `OPEN_UNPROTECTED` deliberately does NOT block the rest of the executor: StateStore.all_open()
  still reports it as locally known, so reconciliation never classifies it `unknown_real` and
  the executor never drops into MANAGE_ONLY over it. Other tickets/symbols continue operating
  normally -- only actions against that exact unprotected ticket are meant to require a fresh,
  explicit human decision, and this module never automates one on its own.
- Broker-side minimum stop-distance (`stops_level`/`freeze_level`) is deliberately NOT
  pre-validated locally -- reliable SymbolInfo isn't available through the current MCP
  connection path used here, and re-deriving that validation from scratch for SL/TP (separate
  from order_planning/limit_price.py's existing LIMIT-price version) was judged higher-risk
  than reusing the retcode-trust doctrine already proven for every other rejection class in
  this project. A `10016` ("Invalid stops") rejection from `modify_position` is therefore
  handled exactly like any other clean, expected-possible failure, not as a bug.

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
   MANAGE_ONLY. See state/policy.py. When reconcile() itself finds unknown_real tickets,
   `_explain_unknown_real()` (Phase 9, root-caused live 2026-08-10 -- see
   docs/PHASE9_FORWARD_TEST_CHECKPOINT.md runs #4/#6) makes ONE extra real get_deals() read to
   check whether any of them are explainable as MT5's own transient SL/TP-close artifact on an
   already-tracked position (state/sl_tp_artifact.py) -- reconcile()'s own ticket-only logic is
   unchanged, and any ticket that isn't fully evidenced this way still trips MANAGE_ONLY exactly
   as before (fail closed, including on any error while gathering this extra evidence).
4. Reads `retcode` out of the raw response via `metatrader_retcodes.parse_trade_response()` --
   never trusts the tool's own `error`/`success` field (Known Issues item 7).
5. Records the true submitted intent (magic/comment/deviation/etc, which MT5 itself will not
   honor) to `StateStore` only after a confirmed-done retcode -- never for a rejected order.
6. Verifies by re-reading real MT5 state and matching by ticket only, never guessing.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from mt5_mcp_trading.domain.models import ExecutionResult, OrderPlan
from mt5_mcp_trading.mcp_adapter.client import McpClient
from mt5_mcp_trading.monitoring.live_performance import infer_deal_time_offset
from mt5_mcp_trading.monitoring.logging_setup import get_logger
from mt5_mcp_trading.mt5_adapter.interfaces import AccountReader
from mt5_mcp_trading.mt5_adapter.mcp_deal_history import McpDealHistoryReader
from mt5_mcp_trading.mt5_adapter.metatrader_retcodes import parse_trade_response
from mt5_mcp_trading.mt5_adapter.safety import NotDemoAccountError, require_demo_account, require_demo_account_kind
from mt5_mcp_trading.state.models import LocalOrderRecord, ReconciliationReport
from mt5_mcp_trading.state.policy import ExecutionPosture, determine_posture
from mt5_mcp_trading.state.reconcile import reconcile
from mt5_mcp_trading.state.sl_tp_artifact import classify_unknown_real_tickets
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


class InvalidOrderPlanError(ValueError):
    """Raised when an OrderPlan fails a local, pre-flight validation check -- before any MCP
    call is made. Distinct from ExecutionBlockedError (posture-based refusal, not about the
    plan's own content) and NotImplementedError (an order_type this executor doesn't support
    at all, rather than a malformed instance of one it does)."""


class SlTpAttachmentFailedError(RuntimeError):
    """Raised by _submit_market() when a MARKET order's position was opened successfully but
    the mandatory SL/TP follow-up (modify_position) could not be confirmed attached -- either
    the call itself failed/raised, returned a non-done retcode, or reported success while a
    fresh live read disagreed. The position is REAL, OPEN, and UNPROTECTED on the account.
    Local state already reflects this (status="OPEN_UNPROTECTED") before this exception is
    ever raised -- see StateStore.record_submission()/mark_sl_tp_attached(). No automatic
    retry or close is attempted by this module; recovery (retry the attach, or close the
    position) requires its own separate, explicitly-approved call."""

    def __init__(
        self, *, ticket: int, order_plan: OrderPlan, reason: str, retcode: Optional[int]
    ) -> None:
        self.ticket = ticket
        self.order_plan = order_plan
        self.reason = reason
        self.retcode = retcode
        super().__init__(
            f"ticket={ticket}: SL/TP attachment failed -- {reason}. Position is OPEN and "
            f"UNPROTECTED; local state is marked OPEN_UNPROTECTED. No automatic retry or "
            f"close was attempted. Requires an explicitly-approved recovery action."
        )


_SL_TP_TOLERANCE = 1e-6  # float-serialization noise only, not a broker-rounding tolerance --
# see this module's docstring on why broker-side stop-distance is not pre-validated here.


def _validate_market_sl_tp(order_plan: OrderPlan) -> None:
    """Pre-flight only -- no MCP call has been made yet when this runs. Rejects missing,
    zero, or wrong-side SL/TP locally rather than spending a round-trip to let MT5's own
    send_order() validation (same side-correctness rules, confirmed by reading
    metatrader_client/order/send_order.py) reject it server-side. Does NOT validate minimum
    distance from the current price (stops_level/freeze_level) -- see this module's docstring."""
    if order_plan.sl <= 0 or order_plan.tp <= 0:
        raise InvalidOrderPlanError(
            f"MARKET order requires both sl>0 and tp>0 (mandatory SL/TP for this project's "
            f"MARKET path) -- got sl={order_plan.sl}, tp={order_plan.tp}. place_market_order "
            f"cannot carry SL/TP at placement, and this executor never submits a MARKET "
            f"order it cannot immediately attempt to protect via modify_position."
        )
    price = order_plan.price
    if price is None:
        raise InvalidOrderPlanError(
            "MARKET order requires OrderPlan.price as a reference for SL/TP side validation, "
            "got None"
        )
    if order_plan.side == "BUY":
        if not (order_plan.sl < price < order_plan.tp):
            raise InvalidOrderPlanError(
                f"BUY MARKET order requires sl < price < tp -- got sl={order_plan.sl}, "
                f"price={price}, tp={order_plan.tp}"
            )
    else:
        if not (order_plan.tp < price < order_plan.sl):
            raise InvalidOrderPlanError(
                f"SELL MARKET order requires tp < price < sl -- got tp={order_plan.tp}, "
                f"price={price}, sl={order_plan.sl}"
            )


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

        if report.unknown_real and local_open:
            # local_open empty => no candidate position any deal could possibly link to, so
            # skip the extra real call entirely rather than pay for a check that cannot
            # possibly explain anything (see state/sl_tp_artifact.py's linkage requirement).
            report = await self._explain_unknown_real(report, local_open)

        return _PostureCheck(determine_posture(report, None), report, None)

    async def _explain_unknown_real(
        self, report: ReconciliationReport, local_open: Sequence[LocalOrderRecord],
    ) -> ReconciliationReport:
        """Second-pass, evidence-based check over report.unknown_real ONLY -- reconcile()'s own
        ticket-only result is never modified, this builds a narrower report from it. See
        state/sl_tp_artifact.py's module docstring for exactly what evidence is required before
        a ticket is excluded. Any failure gathering that evidence (a raised exception from the
        get_deals() call, parsing, anything) leaves `report` completely unchanged -- fails
        closed, exactly like every other safety-critical computation in this codebase that
        can't confirm its own inputs (see e.g. _daily_loss_decision_for_cycle() in
        scripts/run_demo_execution_pipeline_loop.py for the same pattern).

        A ticket classified `explained` here is never adopted -- no StateStore record is ever
        written for the artifact ticket itself. Its underlying, already-locally-owned position
        IS reconciled to CLOSED (record_closed(), a local-only write, no MCP call) using the
        gathered evidence -- the same reconciliation this project has always done manually after
        an incident of this shape (see e.g. the 2026-08-10 checkpoint entries), now automatic
        and evidence-backed instead of a separate later step."""
        try:
            all_records = self._state_store.all_records()
            now = datetime.now(timezone.utc)
            fetch_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            fetch_to = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            deals = await McpDealHistoryReader(self._client).get_deals(
                from_date=fetch_from, to_date=fetch_to,
            )
            offset = infer_deal_time_offset(all_records, deals) or timedelta(0)
            classification = classify_unknown_real_tickets(
                report.unknown_real, local_open, deals, deal_time_offset=offset,
            )
        except Exception as exc:
            _logger.warning(
                "Could not evaluate unknown_real=%r for known SL/TP-close artifacts -- leaving "
                "posture unchanged (fail closed): %r", report.unknown_real, exc,
            )
            return report

        for ev in classification.evidence:
            _logger.info(
                "unknown_real ticket=%d explained as a %s-close artifact of locally-known "
                "position=%d (deal=%d, price=%s matches requested_%s=%s) -- reconciling "
                "position=%d to CLOSED, excluding artifact ticket from unknown_real",
                ev.ticket, ev.kind, ev.position_ticket, ev.deal_ticket, ev.price, ev.kind,
                ev.reference_price, ev.position_ticket,
            )
            self._state_store.record_closed(
                ev.position_ticket,
                reason=(
                    f"auto-reconciled: {ev.kind.upper()} close confirmed via deal={ev.deal_ticket} "
                    f"(order={ev.order_ticket}), price={ev.price} matches requested_{ev.kind}="
                    f"{ev.reference_price}"
                ),
                closed_at=ev.time,
            )

        for rej in classification.rejections:
            # Diagnostic only -- never affects the decision itself, which is already final by
            # this point. Purely so a real MANAGE_ONLY trip is understandable from the log alone
            # (which single check failed), instead of manually re-deriving it after the fact
            # against a fresh get_deals() read the way every prior incident (runs #4/#6) required.
            _logger.warning(
                "unknown_real ticket=%d still unexplained -- %s", rej.ticket, rej.reason,
            )

        return dataclasses.replace(report, unknown_real=classification.unexplained)

    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        await self._gate()

        if order_plan.order_type not in ("LIMIT", "MARKET"):
            raise NotImplementedError(
                f"McpOrderExecutor.submit() only supports LIMIT and MARKET orders, got "
                f"{order_plan.order_type!r}."
            )

        check = await self._current_posture()
        if check.posture is not ExecutionPosture.NORMAL:
            raise ExecutionBlockedError(
                f"Refusing to submit a new order: execution posture is "
                f"{check.posture.value} ({check.report or check.error!r}). See state/policy.py."
            )

        if order_plan.order_type == "MARKET":
            return await self._submit_market(order_plan)
        return await self._submit_limit(order_plan)

    async def _submit_limit(self, order_plan: OrderPlan) -> ExecutionResult:
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

    async def _submit_market(self, order_plan: OrderPlan) -> ExecutionResult:
        _validate_market_sl_tp(order_plan)  # raises InvalidOrderPlanError -- no MCP call yet

        side = order_plan.side  # "BUY"/"SELL" -- matches place_market_order's `type` directly
        raw = await self._client.call_tool(
            "place_market_order",
            {"symbol": order_plan.symbol, "volume": order_plan.volume, "type": side},
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

        # Written BEFORE the SL/TP-attach attempt below -- if the process dies right here, a
        # real unprotected position is still visible in local state, never silently lost. See
        # this module's docstring.
        self._state_store.record_submission(
            ticket=ticket, strategy=strategy_name_for_magic(order_plan.magic),
            magic=order_plan.magic, comment=order_plan.comment, symbol=order_plan.symbol,
            side=side, order_type=order_plan.order_type, requested_volume=order_plan.volume,
            requested_price=order_plan.price, requested_sl=order_plan.sl,
            requested_tp=order_plan.tp, requested_deviation=order_plan.deviation,
            requested_filling_mode=order_plan.filling_mode, requested_expiry=order_plan.expiry,
            retcode=response.retcode, executed_price=executed_price,
            executed_volume=executed_volume, broker_comment=response.tool_message,
            submitted_at=submitted_at, status="OPEN_UNPROTECTED",
        )

        verified_open, open_details = await self._verify_position_present(
            ticket, order_plan.symbol
        )

        # Exactly one modify_position attempt -- no retry, regardless of outcome. A raised
        # exception here (dropped connection, etc.) is exactly as dangerous as a rejected
        # retcode: the position is real and open either way, so both paths raise the same
        # SlTpAttachmentFailedError rather than letting the raw exception propagate unlabeled.
        try:
            sltp_raw = await self._client.call_tool(
                "modify_position",
                {"id": ticket, "stop_loss": order_plan.sl, "take_profit": order_plan.tp},
            )
        except Exception as exc:
            raise SlTpAttachmentFailedError(
                ticket=ticket, order_plan=order_plan,
                reason=f"modify_position call raised: {exc!r}", retcode=None,
            ) from exc

        sltp_response = parse_trade_response(sltp_raw)
        if not sltp_response.done:
            raise SlTpAttachmentFailedError(
                ticket=ticket, order_plan=order_plan,
                reason=(
                    f"modify_position not confirmed -- retcode={sltp_response.retcode}, "
                    f"message={sltp_response.tool_message!r}"
                ),
                retcode=sltp_response.retcode,
            )

        verified_attached, attach_details = await self._verify_sl_tp_attached(
            ticket, order_plan.symbol, order_plan.sl, order_plan.tp
        )
        if not verified_attached:
            raise SlTpAttachmentFailedError(
                ticket=ticket, order_plan=order_plan,
                reason=(
                    f"modify_position reported retcode={sltp_response.retcode} but a fresh "
                    f"live read disagrees: {attach_details}"
                ),
                retcode=sltp_response.retcode,
            )

        self._state_store.mark_sl_tp_attached(ticket)

        return ExecutionResult(
            order_plan=order_plan, success=True, retcode=response.retcode, ticket=ticket,
            deal=deal, executed_price=executed_price, executed_volume=executed_volume,
            broker_comment=response.tool_message, verified=verified_open and verified_attached,
            verification_details=f"{open_details}; {attach_details}",
        )

    async def _verify_position_present(
        self, ticket: int, symbol: str, attempts: int = 3
    ) -> tuple[bool, str]:
        for attempt in range(attempts):
            positions = await self._account.get_positions(symbol=symbol)
            if any(p.ticket == ticket for p in positions):
                return True, (
                    f"ticket={ticket} confirmed present via get_positions_with_magic "
                    f"(attempt {attempt + 1}/{attempts})"
                )
            if attempt < attempts - 1:
                await asyncio.sleep(0.5)
        return False, (
            f"ticket={ticket} NOT found via get_positions_with_magic after {attempts} "
            f"attempts -- submission retcode reported success, but state cannot confirm it"
        )

    async def _verify_sl_tp_attached(
        self, ticket: int, symbol: str, expected_sl: float, expected_tp: float,
        attempts: int = 3,
    ) -> tuple[bool, str]:
        for attempt in range(attempts):
            positions = await self._account.get_positions(symbol=symbol)
            position = next((p for p in positions if p.ticket == ticket), None)
            if position is not None:
                sl_ok = abs(position.sl - expected_sl) < _SL_TP_TOLERANCE
                tp_ok = abs(position.tp - expected_tp) < _SL_TP_TOLERANCE
                if sl_ok and tp_ok:
                    return True, (
                        f"ticket={ticket} confirmed sl={position.sl}/tp={position.tp} match "
                        f"requested sl={expected_sl}/tp={expected_tp} "
                        f"(attempt {attempt + 1}/{attempts})"
                    )
                if attempt == attempts - 1:
                    return False, (
                        f"ticket={ticket} live sl={position.sl}/tp={position.tp} does not "
                        f"match requested sl={expected_sl}/tp={expected_tp} after "
                        f"{attempts} attempts"
                    )
            if attempt < attempts - 1:
                await asyncio.sleep(0.5)
        return False, (
            f"ticket={ticket} NOT found via get_positions_with_magic after {attempts} attempts"
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
