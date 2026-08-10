"""
Pure evidence-based classification of `ReconciliationReport.unknown_real` tickets as known
broker-generated SL/TP-close artifacts of an already-tracked, still-locally-open position --
built to close a real, twice-observed gap in Phase 9 Step 7's live forward test (root-caused
live, 2026-08-10, see docs/PHASE9_FORWARD_TEST_CHECKPOINT.md runs #4 and #6).

THE PROBLEM, confirmed by reading real evidence from both incidents, not guessed: when MT5
executes a position's SL or TP, it briefly (~1 second) surfaces its own auto-generated closing
order as a live PENDING order before the fill settles into history. If
McpOrderExecutor._current_posture()'s reconcile() call happens to land inside that ~1-second
window, the closing order's ticket -- which has no local StateStore record, since this project
never places it -- reconciles as `unknown_real`, correctly tripping MANAGE_ONLY per
state/policy.py's "cannot be matched safely" contract. reconcile() itself is deliberately
ticket-only (see reconcile.py's own docstring) and stays that way here -- unchanged. What this
module adds is a SEPARATE, second-pass check, run only against tickets reconcile() already
flagged unknown_real, using additional real evidence (a real get_deals() read) that reconcile()
deliberately never consults.

Evidence required before a ticket is excluded from unknown_real (ALL of the following; any
missing/ambiguous signal leaves the ticket classified unexplained -- fail closed, per this
project's "never guess ownership" discipline, same as reconcile.py/policy.py):

1. Exactly one real Deal whose `order` field equals the candidate ticket AND whose `entry`
   marks it a closing leg (1=out, 2=inout) -- direct order->deal linkage. Zero or more-than-one
   such deal leaves the ticket unexplained (ambiguous evidence is not strong evidence).
2. That deal's `position_id` matches the ticket of a LocalOrderRecord currently in the supplied
   `local_open` set (i.e. StateStore.all_open() at the time of the check) -- the deal closes a
   position this project actually placed and still considers open. This is the one hard
   "ownership" requirement: a ticket can only ever be explained via a REAL link to an ALREADY
   locally-known position, never invented.
3. Symbol match: the deal's symbol equals the local record's symbol.
4. Volume match: the deal's volume equals the local record's executed (or requested, if
   executed is unknown) volume, within float noise.
5. Side match: the deal is on the CLOSING side of the local record's position (a BUY position
   is only ever closed by a SELL deal, and vice versa).
6. The deal's own `comment` explicitly names it an SL or TP execution (matches `^\\[(sl|tp)\\b`,
   case-insensitive -- the exact shape confirmed live in both incidents, e.g. "[sl 65152.39]").
7. The deal's price is consistent with the SAME field the comment names -- an "[sl ...]"
   comment's price must match the local record's own `requested_sl`, a "[tp ...]" comment's
   price must match `requested_tp` (within a tolerance scaled off the reference price itself,
   not a fixed constant, so it stays meaningful across symbols/price scales; see
   _price_matches()). Comment and price are checked TOGETHER, not independently -- a stray
   comment or a coincidental price match alone is not enough.
8. Timestamp sanity: the deal's (offset-corrected) time is not before the local record's own
   `submitted_at` -- a position cannot close before it opened.

None of this treats magic as evidence anywhere (MT5-side magic is always 0 on tickets this
project places -- see docs/mcp_tool_classification.md item 7 -- and Deal.magic is explicitly
UNCONFIRMED for deal history too, per domain/models.py's Deal docstring). None of this creates
or adopts any new StateStore record for the artifact ticket itself -- the artifact ticket never
gets a local record of its own; only the ALREADY-locally-owned position it closes may be
reconciled to CLOSED by the caller, using the evidence returned here. A ticket with insufficient
evidence is returned unexplained, exactly as if this module didn't exist -- reconcile()'s raw
unknown_real result, and therefore MANAGE_ONLY, is the unconditional fallback.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from mt5_mcp_trading.domain.models import Deal
from mt5_mcp_trading.state.models import LocalOrderRecord

# ENUM_DEAL_TYPE (client_history.py's local DealType, confirmed against domain/models.py's Deal
# docstring): BUY=0, SELL=1. A closing deal for a BUY position must be a SELL deal, and vice
# versa -- MT5 always closes a position with the opposite-side deal.
_DEAL_TYPE_BUY = 0
_DEAL_TYPE_SELL = 1

# ENUM_DEAL_ENTRY (domain/models.py's Deal docstring): 0=in, 1=out, 2=inout. Only a
# closing/reducing leg can be the SL/TP-execution deal being explained here.
_CLOSING_ENTRIES = (1, 2)

_SL_TP_COMMENT_RE = re.compile(r"^\[(sl|tp)\b", re.IGNORECASE)

_VOLUME_TOLERANCE = 1e-9
_PRICE_ABS_TOLERANCE = 1e-6  # float-serialization noise floor, never the whole tolerance alone
_PRICE_REL_TOLERANCE = 0.002  # 0.2% of the reference sl/tp price -- generous enough for a stop
# order's real fill slippage, tight enough that an unrelated price can't pass by coincidence;
# scales naturally across symbols/price magnitudes instead of a fixed constant.


@dataclass(frozen=True, slots=True)
class SlTpArtifactEvidence:
    """Why one specific unknown_real ticket was explained -- kept for logging/audit, and to
    tell the caller which locally-known position to reconcile and with what closed_at."""

    ticket: int  # the unknown_real ticket itself (the SL/TP-execution order)
    position_ticket: int  # the already-locally-known position it closed (== deal.position_id)
    kind: str  # "sl" or "tp", taken from the deal's own comment
    deal_ticket: int
    order_ticket: int  # == ticket, kept for an explicit audit trail
    price: float
    reference_price: float  # the local record's requested_sl or requested_tp that price matched
    time: datetime  # deal.time, corrected by deal_time_offset


@dataclass(frozen=True, slots=True)
class SlTpArtifactRejection:
    """Why one specific unknown_real ticket was NOT explained -- diagnostic only, never fed back
    into the accept/reject decision itself (that decision is already final by the time this is
    constructed). Exists purely so a real MANAGE_ONLY trip can be understood from the log line
    alone -- which single check failed -- instead of re-deriving it by hand against a fresh
    get_deals() read after the fact, the way every prior incident (runs #4/#6) had to be."""

    ticket: int
    reason: str


@dataclass(frozen=True, slots=True)
class SlTpArtifactClassification:
    explained: tuple[int, ...]
    unexplained: tuple[int, ...]
    evidence: tuple[SlTpArtifactEvidence, ...]  # one per explained ticket, same order
    rejections: tuple[SlTpArtifactRejection, ...] = ()  # one per unexplained ticket, same order


def _volume_matches(deal_volume: float, record: LocalOrderRecord) -> bool:
    reference = record.executed_volume if record.executed_volume is not None else record.requested_volume
    return abs(deal_volume - reference) < _VOLUME_TOLERANCE


def _is_closing_side(deal_type: int, position_side: str) -> bool:
    if position_side == "BUY":
        return deal_type == _DEAL_TYPE_SELL
    if position_side == "SELL":
        return deal_type == _DEAL_TYPE_BUY
    return False


def _extract_sl_tp_kind(comment: str) -> Optional[str]:
    match = _SL_TP_COMMENT_RE.match(comment or "")
    return match.group(1).lower() if match else None


def _price_matches(price: float, reference: float) -> bool:
    tolerance = max(_PRICE_ABS_TOLERANCE, _PRICE_REL_TOLERANCE * abs(reference))
    return abs(price - reference) <= tolerance


def _explain_one(
    ticket: int,
    closing_deals: Sequence[Deal],
    local_by_ticket: dict[int, LocalOrderRecord],
    deal_time_offset: timedelta,
) -> tuple[Optional[SlTpArtifactEvidence], Optional[str]]:
    """Returns (evidence, None) on success or (None, reason) on rejection -- `reason` is
    diagnostic-only (see SlTpArtifactRejection), never itself part of the decision."""
    if len(closing_deals) != 1:
        reason = (
            "no closing (entry=out/inout) deal found whose order ticket matches this candidate"
            if not closing_deals else
            f"{len(closing_deals)} candidate closing deals found for this order ticket -- "
            f"ambiguous, not strong evidence"
        )
        return None, reason
    deal = closing_deals[0]

    record = local_by_ticket.get(deal.position_id)
    if record is None:
        return None, (
            f"deal closes position_id={deal.position_id}, which is not a currently "
            f"locally-open position (never adopted, never guessed)"
        )

    if record.symbol != deal.symbol:
        return None, f"symbol mismatch: local position={record.symbol!r}, deal={deal.symbol!r}"
    if not _volume_matches(deal.volume, record):
        reference = record.executed_volume if record.executed_volume is not None else record.requested_volume
        return None, f"volume mismatch: local position={reference}, deal={deal.volume}"
    if not _is_closing_side(deal.type, record.side):
        return None, (
            f"deal type={deal.type} is not a valid closing side for local position.side="
            f"{record.side!r}"
        )

    kind = _extract_sl_tp_kind(deal.comment)
    if kind is None:
        return None, f"deal comment does not start with '[sl ' or '[tp ': {deal.comment!r}"

    reference = record.requested_sl if kind == "sl" else record.requested_tp
    if reference is None or reference <= 0:
        return None, f"local position has no usable requested_{kind} (unset or <= 0)"
    if not _price_matches(deal.price, reference):
        return None, f"price {deal.price} does not match requested_{kind}={reference} within tolerance"

    corrected_time = deal.time - deal_time_offset
    if record.submitted_at is not None and corrected_time < record.submitted_at:
        return None, (
            f"deal time {corrected_time} (offset-corrected) precedes local position's own "
            f"submitted_at={record.submitted_at} -- a position cannot close before it opened"
        )

    return SlTpArtifactEvidence(
        ticket=ticket, position_ticket=record.ticket, kind=kind, deal_ticket=deal.ticket,
        order_ticket=deal.order, price=deal.price, reference_price=reference, time=corrected_time,
    ), None


def classify_unknown_real_tickets(
    unknown_real: Sequence[int],
    local_open: Sequence[LocalOrderRecord],
    deals: Sequence[Deal],
    *,
    deal_time_offset: timedelta = timedelta(0),
) -> SlTpArtifactClassification:
    """Pure, no I/O. `local_open` is expected to be exactly the same StateStore.all_open()
    snapshot reconcile() was already called with -- a ticket is only ever explained via a link
    to a position this project STILL considers locally open, never a stale/closed one.
    `deal_time_offset` should be derived the same way every other Deal.time consumer in this
    codebase already does (monitoring/live_performance.py's infer_deal_time_offset()) -- Deal.
    time is broker/server time mislabeled as UTC, not a bug specific to this module."""
    local_by_ticket = {r.ticket: r for r in local_open}

    deals_by_order: dict[int, list[Deal]] = defaultdict(list)
    for deal in deals:
        if deal.entry in _CLOSING_ENTRIES:
            deals_by_order[deal.order].append(deal)

    explained: list[int] = []
    unexplained: list[int] = []
    evidence: list[SlTpArtifactEvidence] = []
    rejections: list[SlTpArtifactRejection] = []

    for ticket in unknown_real:
        result, reason = _explain_one(
            ticket, deals_by_order.get(ticket, []), local_by_ticket, deal_time_offset,
        )
        if result is not None:
            explained.append(ticket)
            evidence.append(result)
        else:
            unexplained.append(ticket)
            rejections.append(SlTpArtifactRejection(ticket=ticket, reason=reason or "unexplained"))

    return SlTpArtifactClassification(
        explained=tuple(explained), unexplained=tuple(unexplained), evidence=tuple(evidence),
        rejections=tuple(rejections),
    )
