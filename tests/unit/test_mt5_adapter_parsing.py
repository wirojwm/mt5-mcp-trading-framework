"""
Uses response text captured verbatim from the live Phase 3 verification run against the real
metatrader-mcp-server (see docs/mcp_tool_classification.md) as fixtures -- not fabricated
samples -- so these tests prove the parser handles the server's actual output shape.
"""

from __future__ import annotations

from datetime import datetime, timezone

from mt5_mcp_trading.mt5_adapter.metatrader_parsing import (
    parse_dataframe_csv,
    parse_iso_datetime,
    parse_symbol_filling_modes,
)

REAL_CANDLES_CSV = (
    ",time,open,high,low,close,tick_volume,spread,real_volume\n"
    "4,2026-08-01 15:46:00+00:00,63026.71,63026.96,63025.3,63025.53,37,1500,0\n"
    "3,2026-08-01 15:45:00+00:00,63023.12,63027.19,63023.05,63026.71,73,1500,0\n"
    "2,2026-08-01 15:44:00+00:00,63022.6,63024.59,63022.6,63023.13,70,1500,0\n"
    "1,2026-08-01 15:43:00+00:00,63022.65,63022.73,63022.55,63022.62,71,1500,0\n"
    "0,2026-08-01 15:42:00+00:00,63022.96,63023.59,63019.57,63022.65,92,1500,0\n"
)

REAL_EMPTY_POSITIONS_CSV = ",id,time,symbol,type,volume,open,stop_loss,take_profit,profit\n\n"


def test_parse_iso_datetime_handles_zulu_suffix() -> None:
    # get_symbol_price's actual time format, e.g. "2026-08-01T15:46:35Z"
    result = parse_iso_datetime("2026-08-01T15:46:35Z")
    assert result == datetime(2026, 8, 1, 15, 46, 35, tzinfo=timezone.utc)


def test_parse_iso_datetime_handles_explicit_offset() -> None:
    # candle/position/order CSV's actual time format
    result = parse_iso_datetime("2026-08-01 15:46:00+00:00")
    assert result == datetime(2026, 8, 1, 15, 46, 0, tzinfo=timezone.utc)


def test_parse_dataframe_csv_on_real_candles_output() -> None:
    rows = parse_dataframe_csv(REAL_CANDLES_CSV)
    assert len(rows) == 5
    assert rows[0]["close"] == "63025.53"  # newest-first in the raw response, as captured
    assert rows[-1]["close"] == "63022.65"


def test_parse_dataframe_csv_ignores_the_leading_index_column() -> None:
    rows = parse_dataframe_csv(REAL_CANDLES_CSV)
    # the blank-named index column ('' -> '4', '3', ...) must not break field access
    assert rows[0]["time"] == "2026-08-01 15:46:00+00:00"


def test_parse_dataframe_csv_on_real_empty_positions_output() -> None:
    assert parse_dataframe_csv(REAL_EMPTY_POSITIONS_CSV) == []


def test_parse_dataframe_csv_on_empty_string() -> None:
    assert parse_dataframe_csv("") == []


def test_parse_symbol_filling_modes_fok_only() -> None:
    assert parse_symbol_filling_modes(1) == ("FOK",)


def test_parse_symbol_filling_modes_ioc_only() -> None:
    assert parse_symbol_filling_modes(2) == ("IOC",)


def test_parse_symbol_filling_modes_both() -> None:
    assert parse_symbol_filling_modes(3) == ("FOK", "IOC")


def test_parse_symbol_filling_modes_none_supported() -> None:
    assert parse_symbol_filling_modes(0) == ()


def test_parse_symbol_filling_modes_keeps_unknown_bit_visible() -> None:
    # bit 4 isn't in the known FOK(1)/IOC(2) set -- surfaced explicitly, not silently dropped.
    assert parse_symbol_filling_modes(4) == ("UNKNOWN_BIT_4",)
    assert parse_symbol_filling_modes(5) == ("FOK", "UNKNOWN_BIT_4")
