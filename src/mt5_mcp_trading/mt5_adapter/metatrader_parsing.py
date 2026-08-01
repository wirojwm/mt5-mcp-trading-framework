"""
Parsing helpers shared by mcp_market_data.py and mcp_account.py, for metatrader-mcp-server's
two response shapes (confirmed in Phase 3's live verification, not assumed):

- Some tools (get_account_info, get_symbol_price, get_all_symbols) return JSON text.
- Others (get_candles_latest, get_all_positions, get_all_pending_orders, ...) return CSV text
  with a leading empty-header index column (a serialized pandas DataFrame) -- confirmed via
  source (metatrader_client.utils.convert_positions_to_dataframe /
  convert_orders_to_dataframe) and Phase 3's live output.

Time format is inconsistent across tools, also confirmed rather than assumed:
get_symbol_price uses "...Z" (Zulu suffix); candle/position/order CSVs use
"...+00:00" (explicit offset). datetime.fromisoformat() only accepts the "Z" form from
Python 3.11+, and this project targets >=3.10, so _parse_iso_datetime() normalizes it
manually rather than relying on version-specific stdlib behavior.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime


def parse_iso_datetime(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def parse_dataframe_csv(text: str) -> list[dict[str, str]]:
    """Parses a serialized-pandas-DataFrame CSV (leading empty-name index column, which this
    just ignores) into a list of {column_name: raw_string_value} dicts, oldest-to-newest
    order NOT guaranteed -- callers that care about order must sort explicitly (see
    mcp_market_data.py, which sorts candles by time; metatrader-mcp-server returns them
    newest-first, the opposite of this project's "bars, most recent last" convention)."""
    stripped = text.strip()
    if not stripped:
        return []
    reader = csv.DictReader(io.StringIO(stripped))
    return list(reader)
