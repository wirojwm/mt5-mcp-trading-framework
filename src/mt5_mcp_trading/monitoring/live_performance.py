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

infer_deal_time_offset()/deal_time_offset (Phase 9 Step 5, live smoke test root-cause, 2026-08-07):
Deal.time is NOT true UTC despite its own docstring's tzinfo=timezone.utc attachment -- confirmed
live, precisely, by cross-checking three deals' reported times against the same tickets'
independently-known true-UTC fill instants (this project's own log timestamps): an exact, repeated
+3:00:00 offset. MT5's deal/order history 'time' field is broker/terminal SERVER time, not literal
epoch/UTC, unlike tick/candle data (confirmed by reading the vendored client's own conversion code:
get_symbol_price.py/get_candles_latest.py both correctly do `tz=timezone.utc`/`utc=True` on a
genuine epoch value; get_deals_as_dataframe.py's `pd.to_datetime(..., unit='s')` omits `utc=True`
entirely -- consistent with a value that isn't genuine epoch in the first place, needing a real
correction rather than just a missing flag). A 3-hour mislabel is exactly wide enough to push
recently-closed deals outside a same-day reset-boundary window, which is the precise mechanism
behind the 2026-08-07 smoke test finding zero deals every cycle despite 3 real, confirmed closes.

Since the real offset is a property of the connected broker/server (unknown in advance, and not
assumed to be a fixed constant since broker server-time conventions can shift with daylight
saving), infer_deal_time_offset() derives it live each session from data already available: any
CLOSED, MARKET-order LocalOrderRecord's own `submitted_at` (true UTC, recorded locally via
`datetime.now(timezone.utc)` at the moment of submission -- see mcp_order_executor.py) is, for a
MARKET order specifically, essentially simultaneous with the real fill instant (unlike a LIMIT
order, which may fill much later and would corrupt the comparison -- deliberately excluded via
`order_type == "MARKET"`). Diffing that against the matching IN-entry Deal's own (mislabeled) time
recovers the real offset directly, no guessing. Rounded to the nearest 15 minutes (real broker
server offsets are always round numbers, never an arbitrary number of minutes) and taken as the
median across every available MARKET-order reference, to filter out ordinary
network/processing-latency noise and any single-record outlier.

realized_pnl_since()/compute_daily_loss_decision() both take an optional `deal_time_offset`
(default `timedelta(0)`, i.e. no correction -- byte-for-bit unchanged behavior for every existing
caller/test) and subtract it from `deal.time` before comparing against `since`, so the reset-
boundary filter compares against the deal's REAL true-UTC instant, not its mislabeled one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
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


@dataclass(frozen=True, slots=True)
class SlippageResult:
    """Signed slippage in price units for one `system_owned` record with both a requested and
    an executed price on file. Positive = unfavorable (paid more than requested on a BUY, sold
    for less than requested on a SELL); negative = favorable; zero = filled exactly as
    requested. MARKET orders are the meaningful case (price moves between decision and fill);
    a nonzero value on a LIMIT order's own requested price is itself a real anomaly worth
    seeing, not filtered out here."""

    ticket: int
    order_type: str
    slippage_price_units: float


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


def compute_slippage(
    records: Sequence[LocalOrderRecord],
) -> tuple[tuple[SlippageResult, ...], tuple[SkippedRecord, ...]]:
    """Signed requested-vs-executed price slippage for every `system_owned` record that has
    both prices on file. `manual_adoption` records never went through a real submission (see
    this module's docstring / state/models.py's LocalOrderRecord docstring), so they carry no
    real "requested" price to compare against -- skipped, not fabricated as zero slippage.
    Records with no `executed_price` (e.g. a cancelled pending order that never filled) are
    skipped for the same reason: there is nothing real to compare."""
    results: list[SlippageResult] = []
    skipped: list[SkippedRecord] = []

    for record in records:
        if record.origin != "system_owned":
            skipped.append(SkippedRecord(
                ticket=record.ticket,
                reason=f"origin={record.origin!r} -- no real submission, no requested price "
                       f"to compare against",
            ))
            continue
        if record.requested_price is None or record.executed_price is None:
            skipped.append(SkippedRecord(
                ticket=record.ticket,
                reason="requested_price or executed_price is None -- order never reached a "
                       "comparable fill",
            ))
            continue

        signed = (
            record.executed_price - record.requested_price if record.side == "BUY"
            else record.requested_price - record.executed_price
        )
        results.append(SlippageResult(
            ticket=record.ticket, order_type=record.order_type, slippage_price_units=signed,
        ))

    return tuple(results), tuple(skipped)


def infer_deal_time_offset(
    closed_records: Sequence[LocalOrderRecord], deals: Sequence[Deal],
) -> Optional[timedelta]:
    """Derives the real correction for Deal.time's UTC mislabel (see this module's docstring) --
    live, from whatever CLOSED MARKET-order records/deals are already available, no guessing and
    no hardcoded constant. Returns None when no usable MARKET-order reference exists yet (e.g. a
    fresh StateStore, or a run that has only ever placed LIMIT orders) -- callers decide their own
    fallback; this function never fabricates a value it can't derive."""
    by_position: dict[int, list[Deal]] = defaultdict(list)
    for deal in deals:
        by_position[deal.position_id].append(deal)

    raw_diffs: list[timedelta] = []
    for record in closed_records:
        if record.order_type != "MARKET":
            continue
        ins = [d for d in by_position.get(record.ticket, []) if d.entry in (0, 2)]
        if not ins:
            continue
        raw_diffs.append(min(d.time for d in ins) - record.submitted_at)

    if not raw_diffs:
        return None

    quarter_hour = timedelta(minutes=15)
    rounded_quarters = round(median(raw_diffs) / quarter_hour)
    return rounded_quarters * quarter_hour


def realized_pnl_since(
    deals: Sequence[Deal], since: datetime, trusted_position_ids: AbstractSet[int],
    deal_time_offset: timedelta = timedelta(0),
) -> float:
    """The real `realized_pnl_since_reset` input risk.daily_loss_guard.check_daily_loss_limit()
    still has no source for (Step 2/3's carried-forward risk). Sums profit+commission+swap+fee
    across every deal whose position_id is in `trusted_position_ids` (StateStore-derived --
    never deal.magic, see Deal's own docstring) and whose CORRECTED time
    (deal.time - deal_time_offset -- see this module's docstring) is at or after `since`.

    `since` must be timezone-aware, matching daily_loss_guard.daily_reset_boundary()'s own
    requirement (its return value is this function's natural `since` argument) -- rejected
    rather than silently compared against Deal.time's guaranteed-aware value. `deal_time_offset`
    defaults to zero (no correction), matching every existing caller's prior behavior exactly."""
    if since.tzinfo is None:
        raise ValueError("realized_pnl_since() requires a timezone-aware `since`")

    return sum(
        deal.profit + deal.commission + deal.swap + deal.fee
        for deal in deals
        if deal.position_id in trusted_position_ids and (deal.time - deal_time_offset) >= since
    )


def compute_daily_loss_decision(
    deals: Sequence[Deal],
    trusted_position_ids: AbstractSet[int],
    now: datetime,
    config: DailyLossLimitConfig,
    deal_time_offset: timedelta = timedelta(0),
) -> RiskDecision:
    """Phase 9 Step 5: the single function a live caller needs each cycle to get a real
    daily-loss-limit decision -- derives the current reset boundary, sums realized P&L since it
    via realized_pnl_since(), and feeds the result into
    risk.daily_loss_guard.check_daily_loss_limit(). `now` must be timezone-aware
    (daily_reset_boundary()'s own requirement). `deals` is expected to already cover at least
    the current reset window -- callers fetch it via
    McpDealHistoryReader.get_deals(from_date=...) scoped to
    daily_reset_boundary(now, config.reset_hour_utc), WIDENED by a safety margin on both ends to
    tolerate Deal.time's UTC mislabel (see this module's docstring) -- narrowing it to exactly the
    reset boundary, as the pre-2026-08-07 caller did, is what caused that day's live smoke test to
    silently miss every real deal. `deal_time_offset` defaults to zero (no correction), matching
    every existing caller's prior behavior exactly."""
    boundary = daily_reset_boundary(now, config.reset_hour_utc)
    pnl = realized_pnl_since(deals, since=boundary, trusted_position_ids=trusted_position_ids,
                              deal_time_offset=deal_time_offset)
    return check_daily_loss_limit(pnl, config)
