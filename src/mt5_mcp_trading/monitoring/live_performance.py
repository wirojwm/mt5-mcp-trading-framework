"""
Pure join/computation logic turning real closed-trade data into the same shapes
backtest/metrics.py already consumes, so a running forward test's real R-multiple
expectancy/drawdown can be checked without guessing or waiting for a manual reconciliation
(Phase 9 Step 4, docs/PHASE9_FORWARD_TEST_CHECKPOINT.md's Design section). No adapter import,
no MCP/MT5 call anywhere in this module -- callers supply StateStore.all_closed()'s records and
an McpDealHistoryReader.get_deals() result, both already real data by the time they arrive here.

Every Deal is joined to a LocalOrderRecord by position_id == record.ticket ONLY -- never by
deal.magic, whose reliability is unconfirmed (see domain/models.py's Deal docstring). A record
with no matching OUT-entry deal, or whose locked sl makes an R-multiple undefined, is reported
as skipped rather than silently dropped or fabricated -- see build_closed_trades()'s
SkippedRecord list.

notional_pnl on the ClosedTrade instances built here holds a REAL, broker-confirmed dollar
figure (profit+commission+swap+fee summed across every matched deal) -- UNLIKE
backtest/ledger.py's own explicitly-uncalibrated use of that same field (price-difference x
volume, informational only, per that module's docstring). This is a deliberate, documented
divergence in what the same field means depending on where a ClosedTrade came from, not a bug.

close_reason is set to a fixed, honest "closed" for every trade built here -- distinguishing a
real SL exit from a real TP exit would need MT5's own ENUM_DEAL_REASON (a raw `reason` field on
the wire), which Deal does not currently model (Step 4's research scope only covered
client_history.py's documented field list). Not resolved here; a real gap if that distinction
is ever needed downstream, flagged rather than guessed at.

compute_daily_loss_decision() (Phase 9 Step 5) is the single function a live caller needs each
cycle to get a real daily-loss-limit RiskDecision -- combines realized_pnl_since() above with
risk.daily_loss_guard.check_daily_loss_limit(). Still pure/no adapter access: callers fetch
`deals` themselves (scoped to the current reset window) before calling this.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import AbstractSet, Optional, Sequence

from mt5_mcp_trading.backtest.ledger import ClosedTrade
from mt5_mcp_trading.domain.models import Deal, RiskDecision
from mt5_mcp_trading.risk.daily_loss_guard import (
    DailyLossLimitConfig,
    check_daily_loss_limit,
    daily_reset_boundary,
)
from mt5_mcp_trading.state.models import LocalOrderRecord


@dataclass(frozen=True, slots=True)
class SkippedRecord:
    """A closed LocalOrderRecord that build_closed_trades() could not turn into a ClosedTrade,
    and why -- reported rather than silently dropped, per this project's own evidentiary
    discipline (see e.g. state/models.py's ReconciliationReport)."""

    ticket: int
    reason: str


@dataclass(frozen=True, slots=True)
class LiveTradeJoinResult:
    trades: tuple[ClosedTrade, ...]
    skipped: tuple[SkippedRecord, ...]


def _weighted_average_price(deals: list[Deal]) -> float:
    total_volume = sum(d.volume for d in deals)
    return sum(d.price * d.volume for d in deals) / total_volume


def build_closed_trades(
    closed_records: Sequence[LocalOrderRecord], deals: Sequence[Deal],
) -> LiveTradeJoinResult:
    """`closed_records` is expected to be exactly StateStore.all_closed()'s output (status ==
    "CLOSED" only) -- this function trusts that contract rather than re-filtering by status
    itself. `deals` should cover at least the time range those records were open across;
    records with no matching deal at all are reported skipped, not treated as an error (a
    caller-supplied date range that doesn't reach far enough back is a caller problem, visible
    in the skip reasons, not a reason to raise).

    entry==2 ("inout", a netting reversal in one fill) counts as BOTH an opening and a closing
    leg for volume-weighting purposes -- the one fill genuinely did both.
    """
    by_position: dict[int, list[Deal]] = defaultdict(list)
    for deal in deals:
        by_position[deal.position_id].append(deal)

    trades: list[ClosedTrade] = []
    skipped: list[SkippedRecord] = []

    for record in closed_records:
        matched = by_position.get(record.ticket, [])
        ins = [d for d in matched if d.entry in (0, 2)]
        outs = [d for d in matched if d.entry in (1, 2)]

        if not outs:
            skipped.append(SkippedRecord(
                ticket=record.ticket,
                reason="no matching OUT-entry deal found -- cannot determine a real close price",
            ))
            continue

        price_open: Optional[float] = _weighted_average_price(ins) if ins else record.executed_price
        if price_open is None:
            skipped.append(SkippedRecord(
                ticket=record.ticket,
                reason="no IN-entry deal and no locally recorded executed_price -- cannot "
                       "determine a real open price",
            ))
            continue

        risk = abs(price_open - record.requested_sl)
        if risk <= 0:
            skipped.append(SkippedRecord(
                ticket=record.ticket,
                reason=f"sl ({record.requested_sl}) equals price_open ({price_open}) -- "
                       f"risk-per-trade is zero, r_multiple is undefined",
            ))
            continue

        price_close = _weighted_average_price(outs)
        signed_pnl_price_units = (
            price_close - price_open if record.side == "BUY" else price_open - price_close
        )
        notional_pnl = sum(d.profit + d.commission + d.swap + d.fee for d in matched)
        opened_at = min((d.time for d in ins), default=record.submitted_at)
        closed_at = max(d.time for d in outs)

        trades.append(ClosedTrade(
            ticket=record.ticket, symbol=record.symbol, side=record.side,  # type: ignore[arg-type]
            volume=record.requested_volume, price_open=price_open, price_close=price_close,
            sl=record.requested_sl, tp=record.requested_tp, magic=record.magic,
            comment=record.comment, opened_at=opened_at, closed_at=closed_at,
            close_reason="closed",  # see module docstring: SL vs TP not derivable here
            r_multiple=signed_pnl_price_units / risk, notional_pnl=notional_pnl,
        ))

    return LiveTradeJoinResult(trades=tuple(trades), skipped=tuple(skipped))


def realized_pnl_since(
    deals: Sequence[Deal], since: datetime, trusted_position_ids: AbstractSet[int],
) -> float:
    """The real `realized_pnl_since_reset` input risk.daily_loss_guard.check_daily_loss_limit()
    still has no source for (Step 2/3's carried-forward risk). Sums profit+commission+swap+fee
    across every deal whose position_id is in `trusted_position_ids` (StateStore-derived --
    never deal.magic, see Deal's own docstring) and whose time is at or after `since`.

    `since` must be timezone-aware, matching daily_loss_guard.daily_reset_boundary()'s own
    requirement (its return value is this function's natural `since` argument) -- rejected
    rather than silently compared against Deal.time's guaranteed-aware value."""
    if since.tzinfo is None:
        raise ValueError("realized_pnl_since() requires a timezone-aware `since`")

    return sum(
        deal.profit + deal.commission + deal.swap + deal.fee
        for deal in deals
        if deal.position_id in trusted_position_ids and deal.time >= since
    )


def compute_daily_loss_decision(
    deals: Sequence[Deal],
    trusted_position_ids: AbstractSet[int],
    now: datetime,
    config: DailyLossLimitConfig,
) -> RiskDecision:
    """Phase 9 Step 5: the single function a live caller needs each cycle to get a real
    daily-loss-limit decision -- derives the current reset boundary, sums realized P&L since it
    via realized_pnl_since(), and feeds the result into
    risk.daily_loss_guard.check_daily_loss_limit(). `now` must be timezone-aware
    (daily_reset_boundary()'s own requirement). `deals` is expected to already cover at least
    the current reset window -- callers fetch it via
    McpDealHistoryReader.get_deals(from_date=...) scoped to
    daily_reset_boundary(now, config.reset_hour_utc)."""
    boundary = daily_reset_boundary(now, config.reset_hour_utc)
    pnl = realized_pnl_since(deals, since=boundary, trusted_position_ids=trusted_position_ids)
    return check_daily_loss_limit(pnl, config)
