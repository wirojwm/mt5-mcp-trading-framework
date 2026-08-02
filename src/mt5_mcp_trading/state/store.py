"""
Local, persistent order-state storage — a durable audit trail, atomically written, never
implicitly trusted over real MT5 state. See models.py's module docstring for what's recorded
and why, and reconcile.py/policy.py for how this gets cross-checked against reality.

Cold start (no file yet) behaves as an empty store, not an error -- the first write creates the
parent directory and file. A file that exists but can't be parsed raises StateLoadError from
every read/write method; callers must treat that as a hard stop (see policy.py), never fall
back to treating it as empty.
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
    """Raised when the local state file exists but cannot be parsed/trusted."""


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


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict[str, LocalOrderRecord]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {
                ticket: _deserialize(int(ticket), data)
                for ticket, data in raw["records"].items()
            }
        except Exception as exc:
            raise StateLoadError(f"Failed to load state file {self._path}: {exc}") from exc

    def _write(self, records: dict[str, LocalOrderRecord]) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "records": {ticket: _serialize(record) for ticket, record in records.items()},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp_path, self._path)  # atomic on both POSIX and Windows

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
    ) -> None:
        records = self._load()
        records[str(ticket)] = LocalOrderRecord(
            ticket=ticket, strategy=strategy, magic=magic, comment=comment, symbol=symbol,
            side=side, order_type=order_type, requested_volume=requested_volume,
            requested_price=requested_price, requested_sl=requested_sl,
            requested_tp=requested_tp, requested_deviation=requested_deviation,
            requested_filling_mode=requested_filling_mode, requested_expiry=requested_expiry,
            retcode=retcode, executed_price=executed_price, executed_volume=executed_volume,
            broker_comment=broker_comment, submitted_at=submitted_at, closed_at=None,
            status="OPEN", closed_reason=None, origin="system_owned",
        )
        self._write(records)

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
        records = self._load()
        records[str(ticket)] = LocalOrderRecord(
            ticket=ticket, strategy="manual_adoption", magic=magic, comment=note, symbol=symbol,
            side=side, order_type="EXTERNAL_POSITION", requested_volume=volume,
            requested_price=price_open, requested_sl=0.0, requested_tp=0.0,
            requested_deviation=0, requested_filling_mode=None, requested_expiry=None,
            retcode=None, executed_price=price_open, executed_volume=volume,
            broker_comment=note, submitted_at=adopted_at, closed_at=None, status="OPEN",
            closed_reason=None, origin="manual_adoption",
        )
        self._write(records)

    def _transition(
        self, ticket: int, status: OrderRecordStatus, reason: str, closed_at: datetime
    ) -> None:
        records = self._load()
        existing = records.get(str(ticket))
        if existing is None:
            _logger.warning(
                "record_%s called for ticket=%s with no local record -- nothing to update",
                status.lower(), ticket,
            )
            return
        records[str(ticket)] = dataclasses.replace(
            existing, status=status, closed_at=closed_at, closed_reason=reason,
        )
        self._write(records)

    def record_cancelled(self, ticket: int, reason: str, closed_at: datetime) -> None:
        self._transition(ticket, "CANCELLED", reason, closed_at)

    def record_closed(self, ticket: int, reason: str, closed_at: datetime) -> None:
        self._transition(ticket, "CLOSED", reason, closed_at)

    def lookup(self, ticket: int) -> Optional[LocalOrderRecord]:
        return self._load().get(str(ticket))

    def all_open(self) -> tuple[LocalOrderRecord, ...]:
        return tuple(r for r in self._load().values() if r.status == "OPEN")
