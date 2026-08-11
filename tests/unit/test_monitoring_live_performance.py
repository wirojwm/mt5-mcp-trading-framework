"""
build_closed_trades()/realized_pnl_since()/compute_daily_loss_decision() against synthetic
StateStore records and synthetic Deal objects only -- no adapter, no MCP/MT5 call, no live data
anywhere in this file, per Phase 9 Step 4/5's own discipline
(docs/PHASE9_FORWARD_TEST_CHECKPOINT.md).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_mcp_trading.backtest.ledger import BacktestLedger
from mt5_mcp_trading.domain.models import Deal
from mt5_mcp_trading.monitoring.live_performance import (
    build_closed_trades,
    compute_daily_loss_decision,
    compute_slippage,
    infer_deal_time_offset,
    realized_pnl_since,
)
from mt5_mcp_trading.risk.daily_loss_guard import DailyLossLimitConfig
from mt5_mcp_trading.state.models import LocalOrderRecord

T0 = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 4, 10, 5, 0, tzinfo=timezone.utc)


def _record(**overrides: object) -> LocalOrderRecord:
    fields: dict[str, object] = dict(
        ticket=171618036, strategy="grid", magic=71101, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="LIMIT", requested_volume=0.01, requested_price=63000.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode="FOK", requested_expiry=None, retcode=10009,
        executed_price=63000.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=T0, closed_at=T1, status="CLOSED", closed_reason="closed",
        origin="system_owned",
    )
    fields.update(overrides)
    return LocalOrderRecord(**fields)  # type: ignore[arg-type]


def _deal(**overrides: object) -> Deal:
    fields: dict[str, object] = dict(
        ticket=1, order=1, position_id=171618036, time=T0, type=0, entry=0, symbol="BTCUSD",
        volume=0.01, price=63000.0, profit=0.0, commission=-0.06, swap=0.0, fee=0.0, magic=0,
        comment="",
    )
    fields.update(overrides)
    return Deal(**fields)  # type: ignore[arg-type]


# ---------- build_closed_trades ----------

def test_matches_by_position_id_and_computes_buy_r_multiple() -> None:
    record = _record(side="BUY", requested_sl=62000.0, requested_tp=64000.0)
    deals = [
        _deal(entry=0, time=T0, price=63000.0),
        _deal(entry=1, time=T1, price=64000.0),
    ]
    result = build_closed_trades([record], deals)

    assert result.skipped == ()
    assert len(result.trades) == 1
    trade = result.trades[0]
    # risk = |63000 - 62000| = 1000; pnl = 64000 - 63000 = 1000; r = 1.0
    assert trade.r_multiple == pytest.approx(1.0)
    assert trade.ticket == 171618036
    assert (trade.price_open, trade.price_close) == (63000.0, 64000.0)
    assert trade.opened_at == T0
    assert trade.closed_at == T1


def test_computes_sell_r_multiple() -> None:
    record = _record(side="SELL", requested_sl=64000.0, requested_tp=62000.0)
    deals = [
        _deal(entry=0, time=T0, price=63000.0),
        _deal(entry=1, time=T1, price=62000.0),
    ]
    trade = build_closed_trades([record], deals).trades[0]
    # risk = |63000 - 64000| = 1000; pnl = 63000 - 62000 = 1000 (SELL profits on price falling)
    assert trade.r_multiple == pytest.approx(1.0)


def test_r_multiple_matches_backtest_ledger_formula() -> None:
    # Cross-checks the claim in this module's docstring -- live and backtested R-multiples use
    # the exact same formula -- rather than only asserting it in prose.
    ledger = BacktestLedger()
    ticket = ledger.add_open_position(
        symbol="BTCUSD", side="BUY", volume=0.01, price_open=63000.0, sl=62000.0, tp=64000.0,
        magic=71101, comment="grid", opened_at=T0,
    )
    backtest_trade = ledger.close_position(ticket, price_close=63700.0, reason="TP", closed_at=T1)

    record = _record(side="BUY", requested_sl=62000.0, requested_tp=64000.0)
    deals = [_deal(entry=0, time=T0, price=63000.0), _deal(entry=1, time=T1, price=63700.0)]
    live_trade = build_closed_trades([record], deals).trades[0]

    assert live_trade.r_multiple == pytest.approx(backtest_trade.r_multiple)


def test_notional_pnl_sums_real_dollar_figures_across_every_matched_deal() -> None:
    record = _record()
    deals = [
        _deal(entry=0, time=T0, price=63000.0, profit=0.0, commission=-0.06, swap=0.0, fee=0.0),
        _deal(entry=1, time=T1, price=64000.0, profit=12.34, commission=-0.06, swap=-1.2, fee=0.0),
    ]
    trade = build_closed_trades([record], deals).trades[0]
    assert trade.notional_pnl == pytest.approx(0.0 - 0.06 + 0.0 + 0.0 + 12.34 - 0.06 - 1.2 + 0.0)


def test_ignores_deal_magic_and_uses_the_record_magic_instead() -> None:
    record = _record(magic=71101)
    deals = [
        _deal(entry=0, time=T0, price=63000.0, magic=0),  # MT5's own magic=0 quirk
        _deal(entry=1, time=T1, price=64000.0, magic=999999),  # obviously wrong if trusted
    ]
    trade = build_closed_trades([record], deals).trades[0]
    assert trade.magic == 71101  # from the local record, never from either deal


def test_falls_back_to_executed_price_when_no_in_entry_deal() -> None:
    record = _record(executed_price=63000.0)
    deals = [_deal(entry=1, time=T1, price=64000.0)]  # only an OUT deal, no IN deal at all
    trade = build_closed_trades([record], deals).trades[0]
    assert trade.price_open == 63000.0


def test_inout_entry_counts_as_both_the_open_and_close_leg() -> None:
    record = _record()
    deals = [_deal(entry=2, time=T0, price=63500.0)]  # a netting reversal, one fill
    trade = build_closed_trades([record], deals).trades[0]
    assert (trade.price_open, trade.price_close) == (63500.0, 63500.0)
    assert trade.r_multiple == pytest.approx(0.0)


def test_ignores_deals_belonging_to_a_different_position() -> None:
    record = _record(ticket=171618036)
    deals = [
        _deal(position_id=171618036, entry=0, time=T0, price=63000.0),
        _deal(position_id=171618036, entry=1, time=T1, price=64000.0),
        _deal(position_id=999999, entry=1, time=T1, price=1.0),  # unrelated position
    ]
    trade = build_closed_trades([record], deals).trades[0]
    assert trade.price_close == 64000.0  # unaffected by the unrelated deal


def test_skips_record_with_no_matching_out_deal() -> None:
    record = _record()
    deals = [_deal(entry=0, time=T0, price=63000.0)]  # IN only, position never actually closed
    result = build_closed_trades([record], deals)
    assert result.trades == ()
    assert len(result.skipped) == 1
    assert result.skipped[0].ticket == record.ticket
    assert "OUT" in result.skipped[0].reason


def test_skips_record_with_no_deals_at_all() -> None:
    record = _record()
    result = build_closed_trades([record], [])
    assert result.trades == ()
    assert len(result.skipped) == 1


def test_skips_record_with_no_in_deal_and_no_executed_price() -> None:
    record = _record(executed_price=None)
    deals = [_deal(entry=1, time=T1, price=64000.0)]
    result = build_closed_trades([record], deals)
    assert result.trades == ()
    assert len(result.skipped) == 1
    assert "executed_price" in result.skipped[0].reason


def test_skips_record_when_sl_equals_price_open() -> None:
    record = _record(requested_sl=63000.0)  # equals the IN deal's price below
    deals = [_deal(entry=0, time=T0, price=63000.0), _deal(entry=1, time=T1, price=64000.0)]
    result = build_closed_trades([record], deals)
    assert result.trades == ()
    assert len(result.skipped) == 1
    assert "risk-per-trade is zero" in result.skipped[0].reason


def test_multiple_records_each_resolve_independently() -> None:
    record_a = _record(ticket=1, requested_sl=62000.0)
    record_b = _record(ticket=2, requested_sl=63000.0)  # will be skipped -- no OUT deal below
    deals = [
        _deal(position_id=1, entry=0, time=T0, price=63000.0),
        _deal(position_id=1, entry=1, time=T1, price=64000.0),
        _deal(position_id=2, entry=0, time=T0, price=63000.0),
    ]
    result = build_closed_trades([record_a, record_b], deals)
    assert {t.ticket for t in result.trades} == {1}
    assert {s.ticket for s in result.skipped} == {2}


# ---------- compute_slippage ----------

def test_buy_paying_more_than_requested_is_positive_unfavorable_slippage() -> None:
    record = _record(side="BUY", requested_price=63000.0, executed_price=63005.0)
    results, skipped = compute_slippage([record])
    assert skipped == ()
    assert len(results) == 1
    assert results[0].ticket == record.ticket
    assert results[0].slippage_price_units == pytest.approx(5.0)


def test_buy_paying_less_than_requested_is_negative_favorable_slippage() -> None:
    record = _record(side="BUY", requested_price=63000.0, executed_price=62995.0)
    results, _ = compute_slippage([record])
    assert results[0].slippage_price_units == pytest.approx(-5.0)


def test_sell_filling_lower_than_requested_is_positive_unfavorable_slippage() -> None:
    record = _record(side="SELL", requested_price=63000.0, executed_price=62995.0)
    results, _ = compute_slippage([record])
    assert results[0].slippage_price_units == pytest.approx(5.0)


def test_exact_fill_is_zero_slippage() -> None:
    record = _record(requested_price=63000.0, executed_price=63000.0)
    results, _ = compute_slippage([record])
    assert results[0].slippage_price_units == pytest.approx(0.0)


def test_manual_adoption_record_is_skipped_not_fabricated() -> None:
    record = _record(origin="manual_adoption", retcode=None)
    results, skipped = compute_slippage([record])
    assert results == ()
    assert len(skipped) == 1
    assert skipped[0].ticket == record.ticket


def test_record_with_no_executed_price_is_skipped() -> None:
    record = _record(executed_price=None)
    results, skipped = compute_slippage([record])
    assert results == ()
    assert len(skipped) == 1


def test_record_with_no_requested_price_is_skipped() -> None:
    record = _record(requested_price=None)
    results, skipped = compute_slippage([record])
    assert results == ()
    assert len(skipped) == 1


def test_multiple_records_resolve_independently() -> None:
    ok = _record(ticket=1, requested_price=63000.0, executed_price=63005.0)
    bad = _record(ticket=2, executed_price=None)
    results, skipped = compute_slippage([ok, bad])
    assert {r.ticket for r in results} == {1}
    assert {s.ticket for s in skipped} == {2}


# ---------- infer_deal_time_offset ----------

def test_infer_deal_time_offset_returns_none_with_no_records() -> None:
    assert infer_deal_time_offset([], []) is None


def test_infer_deal_time_offset_ignores_limit_orders() -> None:
    # Only a LIMIT record exists -- submitted_at may not be close to the real fill instant for a
    # LIMIT order (it can fill much later), so this must never be used as a reference.
    record = _record(order_type="LIMIT", submitted_at=T0)
    deals = [_deal(entry=0, time=T0 + timedelta(hours=5))]  # would imply a bogus +5h if trusted
    assert infer_deal_time_offset([record], deals) is None


def test_infer_deal_time_offset_derives_offset_from_a_market_order() -> None:
    record = _record(order_type="MARKET", submitted_at=T0)
    deals = [_deal(entry=0, time=T0 + timedelta(hours=3))]  # mislabeled +3h, like the real bug
    assert infer_deal_time_offset([record], deals) == timedelta(hours=3)


def test_infer_deal_time_offset_rounds_to_the_nearest_15_minutes() -> None:
    record = _record(order_type="MARKET", submitted_at=T0)
    # 3h07m of raw noise -- closer to 3h00m than 3h15m on a 15-minute grid.
    deals = [_deal(entry=0, time=T0 + timedelta(hours=3, minutes=7))]
    assert infer_deal_time_offset([record], deals) == timedelta(hours=3)


def test_infer_deal_time_offset_uses_median_to_ignore_an_outlier() -> None:
    records = [
        _record(ticket=1, order_type="MARKET", submitted_at=T0),
        _record(ticket=2, order_type="MARKET", submitted_at=T0),
        _record(ticket=3, order_type="MARKET", submitted_at=T0),
    ]
    deals = [
        _deal(position_id=1, entry=0, time=T0 + timedelta(hours=3)),
        _deal(position_id=2, entry=0, time=T0 + timedelta(hours=3)),
        _deal(position_id=3, entry=0, time=T0 + timedelta(hours=9)),  # a single wild outlier
    ]
    assert infer_deal_time_offset(records, deals) == timedelta(hours=3)


# ---------- realized_pnl_since ----------

def test_realized_pnl_since_sums_matching_deals_at_or_after_since() -> None:
    deals = [
        _deal(position_id=1, time=T0, profit=10.0, commission=-1.0, swap=-0.5, fee=0.0),
        _deal(position_id=1, time=T1, profit=5.0, commission=-0.2, swap=0.0, fee=0.0),
    ]
    total = realized_pnl_since(deals, since=T0, trusted_position_ids={1})
    assert total == pytest.approx(8.5 + 4.8)


def test_realized_pnl_since_excludes_deals_before_since() -> None:
    deals = [
        _deal(position_id=1, time=T0, profit=10.0, commission=0.0, swap=0.0, fee=0.0),
        _deal(position_id=1, time=T1, profit=5.0, commission=0.0, swap=0.0, fee=0.0),
    ]
    total = realized_pnl_since(deals, since=T1, trusted_position_ids={1})
    assert total == pytest.approx(5.0)


def test_realized_pnl_since_excludes_untrusted_position_ids() -> None:
    deals = [_deal(position_id=1, time=T0, profit=10.0, commission=0.0, swap=0.0, fee=0.0)]
    total = realized_pnl_since(deals, since=T0, trusted_position_ids={2})
    assert total == pytest.approx(0.0)


def test_realized_pnl_since_never_filters_by_deal_magic() -> None:
    deals = [_deal(position_id=1, time=T0, magic=999999, profit=10.0, commission=0.0, swap=0.0, fee=0.0)]
    total = realized_pnl_since(deals, since=T0, trusted_position_ids={1})
    assert total == pytest.approx(10.0)  # summed regardless of magic


def test_realized_pnl_since_rejects_naive_since() -> None:
    with pytest.raises(ValueError):
        realized_pnl_since([], since=datetime(2026, 8, 4), trusted_position_ids=set())


def test_realized_pnl_since_defaults_to_zero_offset_unchanged_behavior() -> None:
    deals = [_deal(position_id=1, time=T0, profit=10.0, commission=0.0, swap=0.0, fee=0.0)]
    total = realized_pnl_since(deals, since=T0, trusted_position_ids={1})
    assert total == pytest.approx(10.0)


def test_realized_pnl_since_applies_deal_time_offset_before_filtering() -> None:
    # deal.time (mislabeled) is 5 minutes BEFORE `since` -- excluded without correction. Its real
    # instant (deal.time - offset) is 5 minutes AFTER `since` -- must be included once corrected.
    since = T0
    deal_time = T0 - timedelta(minutes=5)
    offset = timedelta(minutes=-10)  # true_time = deal_time - offset = deal_time + 10min
    deals = [_deal(position_id=1, time=deal_time, profit=10.0, commission=0.0, swap=0.0, fee=0.0)]

    excluded = realized_pnl_since(deals, since=since, trusted_position_ids={1})
    included = realized_pnl_since(deals, since=since, trusted_position_ids={1}, deal_time_offset=offset)

    assert excluded == pytest.approx(0.0)
    assert included == pytest.approx(10.0)


# ---------- compute_daily_loss_decision ----------

def test_compute_daily_loss_decision_approves_when_within_limit() -> None:
    deals = [_deal(position_id=1, time=T0, profit=-50.0, commission=0.0, swap=0.0, fee=0.0)]
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)
    decision = compute_daily_loss_decision(deals, {1}, now=T1, config=config)
    assert decision.approved is True


def test_compute_daily_loss_decision_breaches_when_loss_reaches_the_limit() -> None:
    deals = [_deal(position_id=1, time=T0, profit=-500.0, commission=0.0, swap=0.0, fee=0.0)]
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)
    decision = compute_daily_loss_decision(deals, {1}, now=T1, config=config)
    assert decision.approved is False
    assert decision.blocking_guard == "risk.daily_loss_limit"


def test_compute_daily_loss_decision_ignores_deals_before_the_reset_boundary() -> None:
    # T0/T1 are both 2026-08-04; a deal from the day before must not count toward "today"'s loss.
    yesterday = datetime(2026, 8, 3, 23, 0, 0, tzinfo=timezone.utc)
    deals = [_deal(position_id=1, time=yesterday, profit=-5000.0, commission=0.0, swap=0.0, fee=0.0)]
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)
    decision = compute_daily_loss_decision(deals, {1}, now=T1, config=config)
    assert decision.approved is True  # the huge loss is outside today's window, excluded


def test_compute_daily_loss_decision_ignores_untrusted_position_ids() -> None:
    deals = [_deal(position_id=99, time=T0, profit=-5000.0, commission=0.0, swap=0.0, fee=0.0)]
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)
    decision = compute_daily_loss_decision(deals, {1}, now=T1, config=config)
    assert decision.approved is True  # deal belongs to an untracked position, not counted


def test_compute_daily_loss_decision_off_by_default_always_approves() -> None:
    deals = [_deal(position_id=1, time=T0, profit=-999999.0, commission=0.0, swap=0.0, fee=0.0)]
    config = DailyLossLimitConfig(max_daily_loss=None, reset_hour_utc=0)
    decision = compute_daily_loss_decision(deals, {1}, now=T1, config=config)
    assert decision.approved is True


def test_compute_daily_loss_decision_rejects_naive_now() -> None:
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=0)
    with pytest.raises(ValueError):
        compute_daily_loss_decision([], set(), now=datetime(2026, 8, 4), config=config)


def test_compute_daily_loss_decision_applies_deal_time_offset() -> None:
    # Given the real bug's confirmed sign (Deal.time mislabeled +3h ahead, see
    # monitoring/live_performance.py's module docstring), a loss that genuinely happened BEFORE
    # today's reset boundary (yesterday's window) must not be misattributed to today just
    # because the +3h mislabel pushes its apparent timestamp past the boundary.
    reset_hour_utc = 0
    now = datetime(2026, 8, 7, 2, 0, 0, tzinfo=timezone.utc)
    boundary = datetime(2026, 8, 7, 0, 0, 0, tzinfo=timezone.utc)
    true_deal_time = boundary - timedelta(minutes=30)  # genuinely YESTERDAY, before the boundary
    mislabeled_deal_time = true_deal_time + timedelta(hours=3)  # +3h mislabel, like the real bug
    assert mislabeled_deal_time > boundary  # would look like "today" without correction

    deals = [_deal(position_id=1, time=mislabeled_deal_time, profit=-600.0,
                    commission=0.0, swap=0.0, fee=0.0)]
    config = DailyLossLimitConfig(max_daily_loss=500.0, reset_hour_utc=reset_hour_utc)

    uncorrected = compute_daily_loss_decision(deals, {1}, now=now, config=config)
    corrected = compute_daily_loss_decision(
        deals, {1}, now=now, config=config, deal_time_offset=timedelta(hours=3),
    )

    assert uncorrected.approved is False  # wrongly counts yesterday's loss as today's
    assert corrected.approved is True  # correctly excluded once the real offset is applied
