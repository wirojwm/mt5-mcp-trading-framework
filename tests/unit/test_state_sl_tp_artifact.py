"""
classify_unknown_real_tickets(): pure, no I/O -- see state/sl_tp_artifact.py's module docstring
for the full evidence contract this tests against.

Two fixtures are reconstructed from REAL Phase 9 Step 7 live incidents (root-caused live,
2026-08-10, see docs/PHASE9_FORWARD_TEST_CHECKPOINT.md runs #4 and #6), not invented:

- Run #4: unknown_real ticket 171909600 was MT5's own SL-execution order closing locally-known
  grid position 171908077 (a SELL LIMIT, requested_sl=65087.71) -- confirmed live via
  scripts/run_demo_execution_investigate_ticket_171909600.py: order history showed
  comment="[sl 65087.71]", deal history showed deal=99944397/order=171909600/
  position_id=171908077/entry=OUT (an exact price match was observed live for this one).
- Run #6: unknown_real ticket 171922069 was MT5's own SL-execution order closing locally-known
  grid position 171920424 (a BUY LIMIT, requested_sl=65152.39) -- confirmed live via
  scripts/run_demo_execution_investigate_ticket_171922069.py: deal=99952627/order=171922069/
  position_id=171920424/entry=OUT, comment="[sl 65152.39]", but the deal's actual FILL price
  (65152.03) differs slightly from the configured stop price in the comment (65152.39) --  real,
  observed stop-order slippage, exactly why classify_unknown_real_tickets() matches price within
  a tolerance rather than requiring an exact match.

No fixture here claims a live TP-close incident has ever actually been observed (it hasn't) --
the TP tests are deliberately-labeled synthetic, built the same shape as the two real SL
incidents above (a TP-close is symmetric to an SL-close in every way this module checks).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mt5_mcp_trading.domain.models import Deal
from mt5_mcp_trading.state.models import LocalOrderRecord
from mt5_mcp_trading.state.sl_tp_artifact import SlTpArtifactRejection, classify_unknown_real_tickets

_UTC = timezone.utc


def _local(
    ticket: int, *, side: str = "SELL", symbol: str = "BTCUSD", requested_sl: float = 65087.71,
    requested_tp: float = 64500.0, volume: float = 0.01,
    submitted_at: datetime = datetime(2026, 8, 10, 2, 18, 17, tzinfo=_UTC),
) -> LocalOrderRecord:
    return LocalOrderRecord(
        ticket=ticket, strategy="grid", magic=71101, comment="grid_sell", symbol=symbol,
        side=side, order_type="LIMIT", requested_volume=volume, requested_price=65150.0,
        requested_sl=requested_sl, requested_tp=requested_tp, requested_deviation=150,
        requested_filling_mode="FOK", requested_expiry=None, retcode=10009,
        executed_price=65150.0, executed_volume=volume, broker_comment="Request executed",
        submitted_at=submitted_at, closed_at=None, status="OPEN", closed_reason=None,
        origin="system_owned",
    )


def _deal(
    ticket: int, order: int, position_id: int, *, deal_type: int, entry: int = 1,
    symbol: str = "BTCUSD", volume: float = 0.01, price: float, comment: str,
    time: datetime = datetime(2026, 8, 10, 2, 43, 26, tzinfo=_UTC), magic: int = 0,
    profit: float = -0.6,
) -> Deal:
    return Deal(
        ticket=ticket, order=order, position_id=position_id, time=time, type=deal_type,
        entry=entry, symbol=symbol, volume=volume, price=price, profit=profit, commission=0.0,
        swap=0.0, fee=0.0, magic=magic, comment=comment,
    )


# ---------- real-incident fixtures (runs #4 and #6) ----------

def test_run4_incident_sl_close_artifact_is_explained() -> None:
    # Position 171908077: SELL LIMIT, requested_sl=65087.71. A closing deal for a SELL position
    # is a BUY(0) deal -- matches the real order-history "type='0'" observed live.
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    artifact_deal = _deal(
        ticket=88000001, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
        time=datetime(2026, 8, 10, 2, 43, 27, tzinfo=_UTC),
    )

    result = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[artifact_deal],
    )

    assert result.explained == (171909600,)
    assert result.unexplained == ()
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert ev.ticket == 171909600
    assert ev.position_ticket == 171908077
    assert ev.kind == "sl"
    assert ev.deal_ticket == 88000001
    assert ev.price == 65087.71
    assert ev.reference_price == 65087.71


def test_run6_incident_sl_close_artifact_is_explained_despite_fill_slippage() -> None:
    # Position 171920424: BUY LIMIT, requested_sl=65152.39. A closing deal for a BUY position is
    # a SELL(1) deal -- matches the real "type='1'" observed live. The real fill price
    # (65152.03) differs from the configured stop price named in the comment (65152.39) -- real
    # observed slippage, must still be explained (tolerance-based match, not exact).
    position = _local(171920424, side="BUY", requested_sl=65152.39, requested_tp=65400.0)
    artifact_deal = _deal(
        ticket=99952627, order=171922069, position_id=171920424, deal_type=1, entry=1,
        price=65152.03, comment="[sl 65152.39]", profit=-0.28,
        time=datetime(2026, 8, 10, 7, 53, 25, tzinfo=_UTC),
        symbol="BTCUSD",
    )

    result = classify_unknown_real_tickets(
        unknown_real=(171922069,), local_open=[position], deals=[artifact_deal],
    )

    assert result.explained == (171922069,)
    assert result.unexplained == ()
    ev = result.evidence[0]
    assert ev.kind == "sl"
    assert ev.position_ticket == 171920424
    assert ev.price == 65152.03
    assert ev.reference_price == 65152.39


# ---------- synthetic TP-close (symmetric, no live incident observed yet) ----------

def test_synthetic_tp_close_artifact_is_explained() -> None:
    position = _local(171930000, side="BUY", requested_sl=64000.0, requested_tp=66000.0)
    artifact_deal = _deal(
        ticket=1, order=171930999, position_id=171930000, deal_type=1, entry=1,
        price=66000.0, comment="[tp 66000.0]",
    )

    result = classify_unknown_real_tickets(
        unknown_real=(171930999,), local_open=[position], deals=[artifact_deal],
    )

    assert result.explained == (171930999,)
    assert result.evidence[0].kind == "tp"
    assert result.evidence[0].reference_price == 66000.0


def test_synthetic_tp_close_on_sell_position_is_explained() -> None:
    position = _local(171930100, side="SELL", requested_sl=66000.0, requested_tp=64000.0)
    artifact_deal = _deal(
        ticket=2, order=171930199, position_id=171930100, deal_type=0, entry=1,
        price=64000.0, comment="[tp 64000.0]",
    )

    result = classify_unknown_real_tickets(
        unknown_real=(171930199,), local_open=[position], deals=[artifact_deal],
    )

    assert result.explained == (171930199,)
    assert result.evidence[0].kind == "tp"


# ---------- must remain unknown_real: genuinely foreign / ambiguous evidence ----------

def test_genuinely_foreign_ticket_with_no_deal_at_all_stays_unknown_real() -> None:
    position = _local(171908077)
    result = classify_unknown_real_tickets(unknown_real=(999999,), local_open=[position], deals=[])
    assert result.explained == ()
    assert result.unexplained == (999999,)
    assert result.evidence == ()
    assert result.rejections == (SlTpArtifactRejection(ticket=999999, reason=result.rejections[0].reason),)
    assert "no closing" in result.rejections[0].reason


def test_deal_whose_position_id_matches_no_local_record_stays_unknown_real() -> None:
    # Direct order->deal linkage exists, but the position it closes isn't one this project
    # currently considers locally open -- must not be adopted as if it were.
    position = _local(171908077)  # unrelated ticket
    foreign_deal = _deal(
        ticket=1, order=999999, position_id=555555, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    result = classify_unknown_real_tickets(
        unknown_real=(999999,), local_open=[position], deals=[foreign_deal],
    )
    assert result.unexplained == (999999,)
    assert "not a currently locally-open position" in result.rejections[0].reason


def test_deal_with_no_sl_tp_comment_stays_unknown_real() -> None:
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    ambiguous_deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="",  # no [sl ...]/[tp ...] marker at all
    )
    result = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[ambiguous_deal],
    )
    assert result.unexplained == (171909600,)
    assert "does not start with" in result.rejections[0].reason


def test_multiple_matching_deals_for_the_same_order_stays_unknown_real() -> None:
    # Ambiguous evidence (two candidate closing deals for one order) is not strong evidence --
    # fail closed rather than guess which one is real.
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    deal_a = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    deal_b = _deal(
        ticket=2, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    result = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[deal_a, deal_b],
    )
    assert result.unexplained == (171909600,)
    assert "ambiguous" in result.rejections[0].reason


def test_price_far_from_requested_sl_stays_unknown_real() -> None:
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    mismatched_deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=61000.0,  # nowhere near requested_sl -- not real slippage, a real mismatch
        comment="[sl 65087.71]",
    )
    result = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[mismatched_deal],
    )
    assert result.unexplained == (171909600,)
    assert "does not match requested_sl" in result.rejections[0].reason


def test_comment_says_tp_but_price_matches_sl_stays_unknown_real() -> None:
    # Comment and price must agree on WHICH field they're claiming -- a coincidental match on
    # the wrong field is not enough.
    position = _local(171908077, side="SELL", requested_sl=65087.71, requested_tp=64000.0)
    deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71,  # matches requested_sl...
        comment="[tp 64000.0]",  # ...but the comment claims TP
    )
    result = classify_unknown_real_tickets(unknown_real=(171909600,), local_open=[position], deals=[deal])
    assert result.unexplained == (171909600,)
    assert "does not match requested_tp" in result.rejections[0].reason


def test_wrong_closing_side_stays_unknown_real() -> None:
    # Position is SELL; a real closing deal must be BUY(0). A SELL(1) "closing" deal is not a
    # valid close of this position.
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    wrong_side_deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=1, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    result = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[wrong_side_deal],
    )
    assert result.unexplained == (171909600,)
    assert "not a valid closing side" in result.rejections[0].reason


def test_symbol_mismatch_stays_unknown_real() -> None:
    position = _local(171908077, side="SELL", requested_sl=65087.71, symbol="BTCUSD")
    deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]", symbol="XAUUSD",
    )
    result = classify_unknown_real_tickets(unknown_real=(171909600,), local_open=[position], deals=[deal])
    assert result.unexplained == (171909600,)
    assert "symbol mismatch" in result.rejections[0].reason


def test_volume_mismatch_stays_unknown_real() -> None:
    position = _local(171908077, side="SELL", requested_sl=65087.71, volume=0.01)
    deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]", volume=0.05,
    )
    result = classify_unknown_real_tickets(unknown_real=(171909600,), local_open=[position], deals=[deal])
    assert result.unexplained == (171909600,)
    assert "volume mismatch" in result.rejections[0].reason


def test_deal_before_position_submitted_stays_unknown_real() -> None:
    submitted_at = datetime(2026, 8, 10, 2, 18, 17, tzinfo=_UTC)
    position = _local(171908077, side="SELL", requested_sl=65087.71, submitted_at=submitted_at)
    time_travelling_deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
        time=submitted_at - timedelta(hours=1),  # closed before it ever opened -- impossible
    )
    result = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[time_travelling_deal],
    )
    assert result.unexplained == (171909600,)
    assert "precedes local position's own submitted_at" in result.rejections[0].reason


def test_explained_ticket_has_no_rejection_and_unexplained_has_no_evidence() -> None:
    """Every unknown_real ticket lands in exactly one of the two (explained+evidence) /
    (unexplained+rejections) pairings -- never both, never neither."""
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    good_deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    result = classify_unknown_real_tickets(
        unknown_real=(171909600, 424242), local_open=[position], deals=[good_deal],
    )
    assert result.explained == (171909600,)
    assert len(result.evidence) == 1
    # 424242 has no matching deal -- unexplained, contributing exactly one rejection; the
    # explained ticket (171909600) contributes none.
    assert [r.ticket for r in result.rejections] == [424242]

    # A second call where NEITHER ticket is explained -- confirms rejections has exactly one
    # entry per unexplained ticket, in the same order, and evidence stays empty.
    result2 = classify_unknown_real_tickets(
        unknown_real=(424242, 555555), local_open=[position], deals=[],
    )
    assert result2.explained == ()
    assert result2.evidence == ()
    assert [r.ticket for r in result2.rejections] == [424242, 555555]


def test_deal_time_offset_correction_is_applied_before_the_timestamp_sanity_check() -> None:
    """Deal.time is broker/server time mislabeled as UTC (see monitoring/live_performance.py) --
    without correcting for a real offset, a genuinely-valid closing deal could spuriously look
    like it precedes the position's own submitted_at and wrongly stay unknown_real."""
    submitted_at = datetime(2026, 8, 10, 2, 18, 17, tzinfo=_UTC)
    position = _local(171908077, side="SELL", requested_sl=65087.71, submitted_at=submitted_at)
    # Raw deal.time is only 30 minutes after submission, but the real correction (offset=+3h)
    # applied would put the corrected time BEFORE submission -- i.e. this deal is only valid
    # once we know the direction the offset must be subtracted, exercising deal_time_offset as
    # a real input rather than a no-op default.
    deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]", time=submitted_at + timedelta(hours=4),
    )

    no_offset = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[deal],
    )
    assert no_offset.explained == (171909600,)  # +4h raw is still after submitted_at unmodified

    over_corrected = classify_unknown_real_tickets(
        unknown_real=(171909600,), local_open=[position], deals=[deal],
        deal_time_offset=timedelta(hours=5),  # now corrected time precedes submission
    )
    assert over_corrected.unexplained == (171909600,)


# ---------- no adoption / idempotency ----------

def test_explained_ticket_never_appears_as_its_own_position_ticket() -> None:
    """The artifact ticket itself must never be treated as a locally-owned position -- only the
    position it CLOSES (deal.position_id) is ever referenced in the evidence."""
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    result = classify_unknown_real_tickets(unknown_real=(171909600,), local_open=[position], deals=[deal])
    assert result.evidence[0].position_ticket != result.evidence[0].ticket
    assert result.evidence[0].position_ticket == 171908077


def test_classification_is_pure_and_idempotent() -> None:
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    first = classify_unknown_real_tickets(unknown_real=(171909600,), local_open=[position], deals=[deal])
    second = classify_unknown_real_tickets(unknown_real=(171909600,), local_open=[position], deals=[deal])
    assert first == second


def test_mixed_explained_and_unexplained_tickets_are_both_classified_independently() -> None:
    position = _local(171908077, side="SELL", requested_sl=65087.71)
    good_deal = _deal(
        ticket=1, order=171909600, position_id=171908077, deal_type=0, entry=1,
        price=65087.71, comment="[sl 65087.71]",
    )
    result = classify_unknown_real_tickets(
        unknown_real=(171909600, 424242), local_open=[position], deals=[good_deal],
    )
    assert result.explained == (171909600,)
    assert result.unexplained == (424242,)
