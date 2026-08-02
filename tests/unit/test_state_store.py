"""
StateStore: atomic, persistent order-state records. No live MCP/MT5 call anywhere in this
file -- StateStore has no dependency on either.
"""

from __future__ import annotations

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
    store = StateStore(tmp_path / "order_state.json")
    assert store.all_open() == ()
    assert store.lookup(123456) is None


def test_record_submission_round_trips(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _submit(store)

    record = store.lookup(123456)
    assert record is not None
    assert (record.ticket, record.strategy, record.magic, record.status) == (
        123456, "grid", 71101, "OPEN",
    )
    assert record.requested_sl == 62000.0
    assert record.retcode == 10009
    assert store.all_open() == (record,)


def test_status_persists_across_a_fresh_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "order_state.json"
    _submit(StateStore(path))

    reloaded = StateStore(path)  # simulates a process restart
    record = reloaded.lookup(123456)
    assert record is not None
    assert record.status == "OPEN"


def test_record_cancelled_transitions_status(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _submit(store)

    store.record_cancelled(123456, reason="smoke test cleanup", closed_at=_now())

    record = store.lookup(123456)
    assert record is not None
    assert record.status == "CANCELLED"
    assert record.closed_reason == "smoke test cleanup"
    assert store.all_open() == ()  # no longer open


def test_record_closed_transitions_status(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _submit(store)

    store.record_closed(123456, reason="tp hit", closed_at=_now())

    record = store.lookup(123456)
    assert record is not None
    assert record.status == "CLOSED"


def test_record_submission_defaults_to_system_owned_origin(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _submit(store)
    record = store.lookup(123456)
    assert record is not None
    assert record.origin == "system_owned"
    assert record.retcode == 10009  # a real submission always has one


def test_record_manual_adoption_is_never_fabricated_as_system_owned(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
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
    store = StateStore(tmp_path / "order_state.json")
    store.record_cancelled(999999, reason="never existed locally", closed_at=_now())  # must not raise
    assert store.lookup(999999) is None


def test_reading_a_pre_origin_file_defaults_to_system_owned(tmp_path: Path) -> None:
    # Simulates a state file written before the "origin" field existed (Phase 6 Step 4) --
    # must load without error and default to "system_owned", the correct historical fact for
    # every record written by record_submission() before this field was added.
    import json

    path = tmp_path / "order_state.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "records": {
            "123456": {
                "strategy": "grid", "magic": 71101, "comment": "grid_buy", "symbol": "BTCUSD",
                "side": "BUY", "order_type": "LIMIT", "requested_volume": 0.01,
                "requested_price": 63000.0, "requested_sl": 62000.0, "requested_tp": 64000.0,
                "requested_deviation": 150, "requested_filling_mode": "FOK",
                "requested_expiry": None, "retcode": 10009, "executed_price": 63000.0,
                "executed_volume": 0.01, "broker_comment": "Request executed",
                "submitted_at": _now().isoformat(), "closed_at": None, "status": "OPEN",
                "closed_reason": None,
                # deliberately no "origin" key
            }
        },
    }), encoding="utf-8")

    store = StateStore(path)
    record = store.lookup(123456)
    assert record is not None
    assert record.origin == "system_owned"


def test_multiple_tickets_are_independent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "order_state.json")
    _submit(store, ticket=1, strategy="grid", magic=71101)
    _submit(store, ticket=2, strategy="runner", magic=72101)

    store.record_closed(1, reason="closed", closed_at=_now())

    assert store.lookup(1).status == "CLOSED"  # type: ignore[union-attr]
    assert store.lookup(2).status == "OPEN"  # type: ignore[union-attr]
    assert {r.ticket for r in store.all_open()} == {2}


def test_corrupted_file_raises_state_load_error(tmp_path: Path) -> None:
    path = tmp_path / "order_state.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = StateStore(path)

    with pytest.raises(StateLoadError):
        store.all_open()
    with pytest.raises(StateLoadError):
        store.lookup(123456)
    with pytest.raises(StateLoadError):
        _submit(store)  # writes must also refuse -- never silently overwrite a bad file


def test_write_is_atomic_even_if_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "order_state.json"
    store = StateStore(path)
    _submit(store)  # establish a valid file first
    original_bytes = path.read_bytes()

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash during os.replace")

    monkeypatch.setattr("mt5_mcp_trading.state.store.os.replace", _boom)

    with pytest.raises(OSError):
        _submit(store, ticket=2, strategy="runner", magic=72101)

    # the real file must be untouched -- the crash happened before the atomic swap completed
    assert path.read_bytes() == original_bytes
