"""
Local, persistent order-state storage — a durable audit trail, atomically written per ticket,
never implicitly trusted over real MT5 state. See models.py's module docstring for what's
recorded and why, and reconcile.py/policy.py for how this gets cross-checked against reality.

Format: one JSON file per ticket (`<path>/<ticket>.json`) inside the directory named by `path`,
not one big file for the whole store. This replaces an earlier single-file design where every
write did a full load-serialize-write of every ticket ever recorded, making N sequential writes
O(N^2) total real disk I/O -- a genuine scaling problem found and profiled in Phase 7 (see
docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md). Per-ticket writes now touch only their
own file, independent of how many other tickets exist -- O(1) per write. `all_open()` (and any
future "every ticket" operation) still scans the whole directory, O(N), but that's called far
less often than individual writes.

Cold start (no directory yet) behaves as an empty store, not an error -- the first write creates
the directory and that ticket's file. A ticket file that exists but can't be parsed, or whose
embedded `ticket` field disagrees with its own filename, raises StateLoadError from every
read/write method touching that ticket (or, for all_open(), from touching ANY ticket); callers
must treat that as a hard stop (see policy.py), never fall back to treating it as empty or
skipping just the one bad file.

A store written in the old single-file format is NOT read automatically by this module -- see
scripts/migrate_state_store_to_per_ticket_files.py for the one-time, explicit migration.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from mt5_mcp_trading.monitoring.logging_setup import get_logger
from mt5_mcp_trading.state.models import LocalOrderRecord, OrderRecordOrigin, OrderRecordStatus

_SCHEMA_VERSION = 1
_logger = get_logger("mt5_mcp_trading.state.store")


class StateLoadError(RuntimeError):
    """Raised when a local state file exists but cannot be parsed/trusted."""


def _serialize(record: LocalOrderRecord) -> dict:
    data = asdict(record)
    for key in ("submitted_at", "closed_at", "requested_expiry"):
        value = data[key]
        data[key] = value.isoformat() if isinstance(value, datetime) else value
    return data


def _deserialize(ticket: int, data: dict) -> LocalOrderRecord:
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        return datetime.fromisoformat(value) if value else None

    return LocalOrderRecord(
        ticket=ticket,
        strategy=data["strategy"],
        magic=data["magic"],
        comment=data["comment"],
        symbol=data["symbol"],
        side=data["side"],
        order_type=data["order_type"],
        requested_volume=data["requested_volume"],
        requested_price=data["requested_price"],
        requested_sl=data["requested_sl"],
        requested_tp=data["requested_tp"],
        requested_deviation=data["requested_deviation"],
        requested_filling_mode=data["requested_filling_mode"],
        requested_expiry=_parse_dt(data["requested_expiry"]),
        retcode=data["retcode"],
        executed_price=data["executed_price"],
        executed_volume=data["executed_volume"],
        broker_comment=data["broker_comment"],
        submitted_at=_parse_dt(data["submitted_at"]),
        closed_at=_parse_dt(data["closed_at"]),
        status=data["status"],
        closed_reason=data["closed_reason"],
        # Backward-compatible default for records written before "origin" existed (Step 4) --
        # every record written before this field existed genuinely was system-submitted.
        origin=data.get("origin", "system_owned"),
    )


def _record_path(directory: Path, ticket: int) -> Path:
    return directory / f"{ticket}.json"


def _write_record_file(path: Path, record: LocalOrderRecord) -> None:
    payload = {"schema_version": _SCHEMA_VERSION, **_serialize(record)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)  # atomic on both POSIX and Windows


def _read_record_file(path: Path) -> LocalOrderRecord:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ticket = int(path.stem)
        if int(data["ticket"]) != ticket:
            raise ValueError(
                f"record's ticket field {data['ticket']!r} does not match filename {path.name!r}"
            )
        return _deserialize(ticket, data)
    except Exception as exc:
        raise StateLoadError(f"Failed to load state file {path}: {exc}") from exc


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path  # a directory: one <ticket>.json file per ticket

    def _iter_ticket_files(self) -> list[Path]:
        if not self._path.exists():
            return []
        return sorted(p for p in self._path.glob("*.json") if p.stem.isdigit())

    def _load_one(self, ticket: int) -> Optional[LocalOrderRecord]:
        record_path = _record_path(self._path, ticket)
        if not record_path.exists():
            return None
        return _read_record_file(record_path)

    def _load_all(self) -> dict[int, LocalOrderRecord]:
        return {int(p.stem): _read_record_file(p) for p in self._iter_ticket_files()}

    def _write_one(self, record: LocalOrderRecord) -> None:
        _write_record_file(_record_path(self._path, record.ticket), record)

    def record_submission(
        self,
        *,
        ticket: int,
        strategy: str,
        magic: int,
        comment: str,
        symbol: str,
        side: str,
        order_type: str,
        requested_volume: float,
        requested_price: Optional[float],
        requested_sl: float,
        requested_tp: float,
        requested_deviation: int,
        requested_filling_mode: Optional[str],
        requested_expiry: Optional[datetime],
        retcode: int,
        executed_price: Optional[float],
        executed_volume: Optional[float],
        broker_comment: str,
        submitted_at: datetime,
        status: OrderRecordStatus = "OPEN",
    ) -> None:
        """status defaults to "OPEN" (LIMIT orders carry sl/tp at placement, nothing further
        to attach). MARKET submissions (Phase 6 Step 6) pass status="OPEN_UNPROTECTED" -- this
        call must happen before any SL/TP-attach attempt, so a crash between the two leaves an
        accurate record, not a silently-lost one. See mark_sl_tp_attached()."""
        record = LocalOrderRecord(
            ticket=ticket, strategy=strategy, magic=magic, comment=comment, symbol=symbol,
            side=side, order_type=order_type, requested_volume=requested_volume,
            requested_price=requested_price, requested_sl=requested_sl,
            requested_tp=requested_tp, requested_deviation=requested_deviation,
            requested_filling_mode=requested_filling_mode, requested_expiry=requested_expiry,
            retcode=retcode, executed_price=executed_price, executed_volume=executed_volume,
            broker_comment=broker_comment, submitted_at=submitted_at, closed_at=None,
            status=status, closed_reason=None, origin="system_owned",
        )
        self._write_one(record)

    def mark_sl_tp_attached(self, ticket: int) -> None:
        """Transitions a MARKET order's record from OPEN_UNPROTECTED to OPEN. Caller
        (McpOrderExecutor) must only call this after BOTH a confirmed-done modify_position
        retcode AND a fresh live read agree SL/TP now matches what was requested -- never on
        retcode alone. Leaves closed_at/closed_reason untouched: the position isn't closed,
        just now protected."""
        existing = self._load_one(ticket)
        if existing is None:
            _logger.warning(
                "mark_sl_tp_attached called for ticket=%s with no local record -- nothing to "
                "update", ticket,
            )
            return
        self._write_one(dataclasses.replace(existing, status="OPEN"))

    def record_manual_adoption(
        self,
        *,
        ticket: int,
        symbol: str,
        side: str,
        volume: float,
        price_open: float,
        magic: int,
        adopted_at: datetime,
        note: str,
    ) -> None:
        """For a position opened directly in the MT5 terminal, outside McpOrderExecutor
        entirely -- see models.py's module docstring on "manual_adoption". `magic` must be the
        REAL value observed live (not an invented one -- MT5 itself is confirmed to report 0
        on positions this project didn't place, see docs/mcp_tool_classification.md item 7).
        `volume`/`price_open` are independently observed, not requested. `retcode` is always
        None here: no submission ever happened to receive a response from."""
        record = LocalOrderRecord(
            ticket=ticket, strategy="manual_adoption", magic=magic, comment=note, symbol=symbol,
            side=side, order_type="EXTERNAL_POSITION", requested_volume=volume,
            requested_price=price_open, requested_sl=0.0, requested_tp=0.0,
            requested_deviation=0, requested_filling_mode=None, requested_expiry=None,
            retcode=None, executed_price=price_open, executed_volume=volume,
            broker_comment=note, submitted_at=adopted_at, closed_at=None, status="OPEN",
            closed_reason=None, origin="manual_adoption",
        )
        self._write_one(record)

    def _transition(
        self, ticket: int, status: OrderRecordStatus, reason: str, closed_at: datetime
    ) -> None:
        existing = self._load_one(ticket)
        if existing is None:
            _logger.warning(
                "record_%s called for ticket=%s with no local record -- nothing to update",
                status.lower(), ticket,
            )
            return
        self._write_one(dataclasses.replace(
            existing, status=status, closed_at=closed_at, closed_reason=reason,
        ))

    def record_cancelled(self, ticket: int, reason: str, closed_at: datetime) -> None:
        self._transition(ticket, "CANCELLED", reason, closed_at)

    def record_closed(self, ticket: int, reason: str, closed_at: datetime) -> None:
        self._transition(ticket, "CLOSED", reason, closed_at)

    def lookup(self, ticket: int) -> Optional[LocalOrderRecord]:
        return self._load_one(ticket)

    def all_open(self) -> tuple[LocalOrderRecord, ...]:
        # OPEN_UNPROTECTED counts as open for reconciliation purposes -- an unprotected
        # MARKET position must still be recognized as locally known, or it would reconcile as
        # unknown_real and force the whole executor into MANAGE_ONLY (see state/models.py's
        # OrderRecordStatus docstring; only that one ticket is meant to require special
        # handling, never the rest of the executor).
        return tuple(
            r for r in self._load_all().values() if r.status in ("OPEN", "OPEN_UNPROTECTED")
        )
