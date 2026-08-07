"""
StateStore: atomic, persistent order-state records, one JSON file per ticket. No live MCP/MT5
call anywhere in this file -- StateStore has no dependency on either.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mt5_mcp_trading.state.store import StateLoadError, StateStore


def _now() -> datetime:
    return datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


def _submit(store: StateStore, ticket: int = 123456, strategy: str = "grid", magic: int = 71101) -> None:
    store.record_submission(
        ticket=ticket, strategy=strategy, magic=magic, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="LIMIT", requested_volume=0.01, requested_price=63000.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode="FOK", requested_expiry=None, retcode=10009,
        executed_price=63000.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=_now(),
    )


def test_cold_start_has_no_records(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    assert store.all_open() == ()
    assert store.all_closed() == ()
    assert store.all_records() == ()
    assert store.lookup(123456) is None


def test_record_submission_round_trips(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _submit(store)

    record = store.lookup(123456)
    assert record is not None
    assert (record.ticket, record.strategy, record.magic, record.status) == (
        123456, "grid", 71101, "OPEN",
    )
    assert record.requested_sl == 62000.0
    assert record.retcode == 10009
    assert store.all_open() == (record,)


def test_submission_writes_one_file_per_ticket(tmp_path: Path) -> None:
    directory = tmp_path / "order_state"
    store = StateStore(directory)
    _submit(store, ticket=123456)
    _submit(store, ticket=789012, strategy="runner", magic=72101)

    assert (directory / "123456.json").exists()
    assert (directory / "789012.json").exists()
    # writing ticket 789012 must not have touched 123456's file/content
    data = json.loads((directory / "123456.json").read_text(encoding="utf-8"))
    assert data["ticket"] == 123456
    assert data["strategy"] == "grid"


def test_status_persists_across_a_fresh_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "order_state"
    _submit(StateStore(path))

    reloaded = StateStore(path)  # simulates a process restart
    record = reloaded.lookup(123456)
    assert record is not None
    assert record.status == "OPEN"


def test_record_cancelled_transitions_status(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _submit(store)

    store.record_cancelled(123456, reason="smoke test cleanup", closed_at=_now())

    record = store.lookup(123456)
    assert record is not None
    assert record.status == "CANCELLED"
    assert record.closed_reason == "smoke test cleanup"
    assert store.all_open() == ()  # no longer open
    # CANCELLED never filled -- must not be counted as a closed trade
    assert store.all_closed() == ()


def test_record_closed_transitions_status(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _submit(store)

    store.record_closed(123456, reason="tp hit", closed_at=_now())

    record = store.lookup(123456)
    assert record is not None
    assert record.status == "CLOSED"
    assert store.all_open() == ()
    assert store.all_closed() == (record,)


def test_record_submission_with_open_unprotected_status(tmp_path: Path) -> None:
    # Phase 6 Step 6: MARKET submissions pass status="OPEN_UNPROTECTED" explicitly -- proves
    # the parameter round-trips and that OPEN_UNPROTECTED still counts as "open" for
    # reconciliation (see all_open()'s docstring on why: an unprotected position must remain
    # locally known, or it would reconcile as unknown_real and force the whole executor into
    # MANAGE_ONLY over a single ticket).
    store = StateStore(tmp_path / "order_state")
    store.record_submission(
        ticket=555777, strategy="grid", magic=71101, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="MARKET", requested_volume=0.01, requested_price=63005.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode=None, requested_expiry=None, retcode=10009,
        executed_price=63005.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=_now(), status="OPEN_UNPROTECTED",
    )

    record = store.lookup(555777)
    assert record is not None
    assert record.status == "OPEN_UNPROTECTED"
    assert store.all_open() == (record,)  # counted as open, not excluded
    assert store.all_closed() == ()  # not closed


def test_mark_sl_tp_attached_transitions_open_unprotected_to_open(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    store.record_submission(
        ticket=555777, strategy="grid", magic=71101, comment="grid_buy", symbol="BTCUSD",
        side="BUY", order_type="MARKET", requested_volume=0.01, requested_price=63005.0,
        requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
        requested_filling_mode=None, requested_expiry=None, retcode=10009,
        executed_price=63005.0, executed_volume=0.01, broker_comment="Request executed",
        submitted_at=_now(), status="OPEN_UNPROTECTED",
    )

    store.mark_sl_tp_attached(555777)

    record = store.lookup(555777)
    assert record is not None
    assert record.status == "OPEN"
    assert record.closed_at is None  # unlike record_cancelled/record_closed -- not a closure
    assert record.closed_reason is None


def test_mark_sl_tp_attached_on_unknown_ticket_logs_and_does_not_raise(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    store.mark_sl_tp_attached(999999)  # must not raise
    assert store.lookup(999999) is None


def test_record_submission_defaults_to_system_owned_origin(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _submit(store)
    record = store.lookup(123456)
    assert record is not None
    assert record.origin == "system_owned"
    assert record.retcode == 10009  # a real submission always has one


def test_record_manual_adoption_is_never_fabricated_as_system_owned(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    store.record_manual_adoption(
        ticket=171604527, symbol="BTCUSD", side="BUY", volume=0.01, price_open=63440.91,
        magic=0, adopted_at=_now(), note="manually opened by user, adopted after live verification",
    )

    record = store.lookup(171604527)
    assert record is not None
    assert record.origin == "manual_adoption"
    assert record.strategy == "manual_adoption"
    assert record.retcode is None  # no submission ever happened -- never fabricated
    assert record.magic == 0  # the REAL observed magic, not an invented one
    assert record.requested_volume == 0.01  # observed, not requested
    assert record.requested_price == 63440.91  # the real observed open price
    assert record.executed_price == 63440.91
    assert record.status == "OPEN"
    assert store.all_open() == (record,)


def test_transition_on_unknown_ticket_logs_and_does_not_raise(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    store.record_cancelled(999999, reason="never existed locally", closed_at=_now())  # must not raise
    assert store.lookup(999999) is None


def test_reading_a_pre_origin_file_defaults_to_system_owned(tmp_path: Path) -> None:
    # Simulates a ticket file written before the "origin" field existed (Phase 6 Step 4) --
    # must load without error and default to "system_owned", the correct historical fact for
    # every record written by record_submission() before this field was added.
    directory = tmp_path / "order_state"
    directory.mkdir(parents=True)
    (directory / "123456.json").write_text(json.dumps({
        "schema_version": 1,
        "ticket": 123456,
        "strategy": "grid", "magic": 71101, "comment": "grid_buy", "symbol": "BTCUSD",
        "side": "BUY", "order_type": "LIMIT", "requested_volume": 0.01,
        "requested_price": 63000.0, "requested_sl": 62000.0, "requested_tp": 64000.0,
        "requested_deviation": 150, "requested_filling_mode": "FOK",
        "requested_expiry": None, "retcode": 10009, "executed_price": 63000.0,
        "executed_volume": 0.01, "broker_comment": "Request executed",
        "submitted_at": _now().isoformat(), "closed_at": None, "status": "OPEN",
        "closed_reason": None,
        # deliberately no "origin" key
    }), encoding="utf-8")

    store = StateStore(directory)
    record = store.lookup(123456)
    assert record is not None
    assert record.origin == "system_owned"


def test_multiple_tickets_are_independent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state")
    _submit(store, ticket=1, strategy="grid", magic=71101)
    _submit(store, ticket=2, strategy="runner", magic=72101)

    store.record_closed(1, reason="closed", closed_at=_now())

    assert store.lookup(1).status == "CLOSED"  # type: ignore[union-attr]
    assert store.lookup(2).status == "OPEN"  # type: ignore[union-attr]
    assert {r.ticket for r in store.all_open()} == {2}
    assert {r.ticket for r in store.all_closed()} == {1}
    assert {r.ticket for r in store.all_records()} == {1, 2}  # regardless of status


def test_corrupted_ticket_file_raises_state_load_error_only_for_that_ticket(tmp_path: Path) -> None:
    directory = tmp_path / "order_state"
    directory.mkdir(parents=True)
    (directory / "123456.json").write_text("{not valid json", encoding="utf-8")
    store = StateStore(directory)

    with pytest.raises(StateLoadError):
        store.lookup(123456)
    with pytest.raises(StateLoadError):
        store.all_open()  # scans every ticket -- one bad file must still hard-stop the whole read
    with pytest.raises(StateLoadError):
        store.all_closed()  # same full-directory scan, same hard-stop guarantee
    with pytest.raises(StateLoadError):
        store.all_records()  # same full-directory scan, same hard-stop guarantee
    with pytest.raises(StateLoadError):
        store.record_cancelled(123456, reason="test", closed_at=_now())  # must load before writing

    # an unrelated ticket is untouched by the corruption -- writing/reading it must succeed
    _submit(store, ticket=999999)
    assert store.lookup(999999) is not None


def test_ticket_file_whose_content_disagrees_with_its_filename_raises(tmp_path: Path) -> None:
    directory = tmp_path / "order_state"
    directory.mkdir(parents=True)
    _submit(StateStore(directory), ticket=123456)
    # Tamper: rename so the filename no longer matches the embedded "ticket" field.
    (directory / "123456.json").rename(directory / "999999.json")

    store = StateStore(directory)
    with pytest.raises(StateLoadError):
        store.lookup(999999)
    with pytest.raises(StateLoadError):
        store.all_open()


# ---------- Phase 7: at-scale sweep ----------
# Everything above uses 1-2 tickets. reconcile()/all_open() are set/filter operations that
# shouldn't degrade with size, but this project's own culture is "prove it, don't assume" --
# these tests exercise realistic ticket counts and a realistic mixed-status population, not
# toy examples, to actually confirm that rather than take it on faith.

def _submit_n(store: StateStore, n: int, base_ticket: int = 171600000) -> list[int]:
    """Writes n distinct tickets with MT5-realistic large ticket numbers (matching the
    magnitude of real tickets seen in Phase 6, e.g. 171618202), alternating strategy/magic --
    returns the tickets written, in order."""
    tickets = []
    for i in range(n):
        ticket = base_ticket + i
        strategy, magic = ("grid", 71101) if i % 2 == 0 else ("runner", 72101)
        _submit(store, ticket=ticket, strategy=strategy, magic=magic)
        tickets.append(ticket)
    return tickets


def test_many_tickets_round_trip_correctly_with_mixed_statuses(tmp_path: Path) -> None:
    # Each write now touches only its own ticket's file (see store.py's module docstring for
    # the O(N^2)-for-N-writes problem this replaced -- profiled at ~28s wall time for 250
    # single-file writes on this machine, os.replace's per-call overhead dominating). 200 here
    # is well beyond the old 40-ticket ceiling and still runs fast, since per-ticket writes no
    # longer scale with total ticket count.
    store = StateStore(tmp_path / "order_state")
    tickets = _submit_n(store, 200)

    # Realistic mixed population: every 4th ticket cancelled, every 5th closed, every 7th
    # (that isn't already cancelled/closed) left OPEN_UNPROTECTED, the rest stay OPEN.
    expected_status: dict[int, str] = {}
    for i, ticket in enumerate(tickets):
        if i % 4 == 0:
            store.record_cancelled(ticket, reason="test sweep", closed_at=_now())
            expected_status[ticket] = "CANCELLED"
        elif i % 5 == 0:
            store.record_closed(ticket, reason="test sweep", closed_at=_now())
            expected_status[ticket] = "CLOSED"
        elif i % 7 == 0:
            # Simulate a MARKET submission whose status started OPEN_UNPROTECTED -- overwrite
            # the record directly via a fresh record_submission call for this ticket instead,
            # since transitioning requires the OPEN_UNPROTECTED starting point.
            store.record_submission(
                ticket=ticket, strategy="grid", magic=71101, comment="grid_buy", symbol="BTCUSD",
                side="BUY", order_type="MARKET", requested_volume=0.01, requested_price=63000.0,
                requested_sl=62000.0, requested_tp=64000.0, requested_deviation=150,
                requested_filling_mode=None, requested_expiry=None, retcode=10009,
                executed_price=63000.0, executed_volume=0.01, broker_comment="Request executed",
                submitted_at=_now(), status="OPEN_UNPROTECTED",
            )
            expected_status[ticket] = "OPEN_UNPROTECTED"
        else:
            expected_status[ticket] = "OPEN"

    # Reload fresh, simulating a process restart -- every one of the tickets must survive
    # the full write/reload round trip with the exact status expected, not just "most of them".
    reloaded = StateStore(tmp_path / "order_state")
    assert len(tickets) == 200
    for ticket in tickets:
        record = reloaded.lookup(ticket)
        assert record is not None, f"ticket={ticket} vanished after reload"
        assert record.status == expected_status[ticket], f"ticket={ticket}"

    expected_open_tickets = {
        t for t, s in expected_status.items() if s in ("OPEN", "OPEN_UNPROTECTED")
    }
    actual_open_tickets = {r.ticket for r in reloaded.all_open()}
    assert actual_open_tickets == expected_open_tickets
    assert len(reloaded.all_open()) == len(expected_open_tickets)  # no duplicates

    expected_closed_tickets = {t for t, s in expected_status.items() if s == "CLOSED"}
    actual_closed_tickets = {r.ticket for r in reloaded.all_closed()}
    assert actual_closed_tickets == expected_closed_tickets
    assert len(reloaded.all_closed()) == len(expected_closed_tickets)  # no duplicates

    actual_all_tickets = {r.ticket for r in reloaded.all_records()}
    assert actual_all_tickets == set(tickets)  # every ticket, regardless of status
    assert len(reloaded.all_records()) == len(tickets)  # no duplicates


def test_rapid_sequential_mutations_remain_consistent(tmp_path: Path) -> None:
    """A long, rapid burst of sequential submit/cancel/close/mark calls against the SAME store
    instance (no reload in between) is what a real strategy accumulating many trades over time
    actually does (no threading/multiprocessing involved anywhere in this codebase's actual
    usage, so genuine concurrent-access testing doesn't apply; see
    docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md for that scoping decision). Proves no
    update is ever lost, now that each write only touches its own ticket's file."""
    store = StateStore(tmp_path / "order_state")
    tickets = _submit_n(store, 100)  # an order of magnitude beyond the old 30-ticket ceiling

    for i, ticket in enumerate(tickets):
        if i % 3 == 0:
            store.record_cancelled(ticket, reason="burst", closed_at=_now())
        elif i % 3 == 1:
            store.record_closed(ticket, reason="burst", closed_at=_now())
        # i % 3 == 2: left OPEN, untouched

    for i, ticket in enumerate(tickets):
        expected = "CANCELLED" if i % 3 == 0 else "CLOSED" if i % 3 == 1 else "OPEN"
        assert store.lookup(ticket).status == expected, f"ticket={ticket}"  # type: ignore[union-attr]

    expected_open = {tickets[i] for i in range(len(tickets)) if i % 3 == 2}
    assert {r.ticket for r in store.all_open()} == expected_open


def test_write_is_atomic_even_if_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "order_state"
    store = StateStore(directory)
    _submit(store)  # establish a valid file first
    ticket_path = directory / "123456.json"
    original_bytes = ticket_path.read_bytes()

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash during os.replace")

    monkeypatch.setattr("mt5_mcp_trading.state.store.os.replace", _boom)

    with pytest.raises(OSError):
        store.record_cancelled(123456, reason="should not land", closed_at=_now())

    # the real file must be untouched -- the crash happened before the atomic swap completed
    assert ticket_path.read_bytes() == original_bytes


def test_writing_one_ticket_never_touches_another_tickets_file(tmp_path: Path) -> None:
    directory = tmp_path / "order_state"
    store = StateStore(directory)
    _submit(store, ticket=1)
    other_path = directory / "1.json"
    mtime_before = other_path.stat().st_mtime_ns

    _submit(store, ticket=2)  # a second, unrelated write

    assert other_path.stat().st_mtime_ns == mtime_before  # ticket 1's file was never rewritten
