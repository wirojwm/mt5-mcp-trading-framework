"""
Local, persistent historical-bar cache -- Phase 8 Step 2's remaining deliverable
(docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md). Pure file I/O only: no MCP/adapter imports
anywhere in this module (see this package's __init__.py). Seeding the cache from a real MT5
connection is a separate concern, done by a script (scripts/run_demo_execution_historical_data_cache_seed.py),
never by this module.

Format: one CSV file per symbol+timeframe (`cache_path()`), header row
`time,open,high,low,close,tick_volume,spread` -- symbol/timeframe are NOT stored per-row
(they're implied by the filename, matching state/store.py's per-ticket-file convention of
keeping file identity in the path rather than duplicating it inside every row). `time` is
stored as `datetime.isoformat()`; `load_bars()` reads it back with `datetime.fromisoformat()`
directly -- no adapter-layer date-parsing helper is imported here on purpose (see this
package's __init__.py).

Cold start (no file yet) behaves as an empty cache, not an error -- `load_bars()` on a missing
path returns `[]`, matching state/store.py's own "first write creates it" precedent, so a
caller never needs to special-case "have I cached anything yet."
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Sequence

from mt5_mcp_trading.domain.models import MarketBar

_FIELDNAMES = ["time", "open", "high", "low", "close", "tick_volume", "spread"]


def cache_path(base_dir: Path, symbol: str, timeframe: str) -> Path:
    return base_dir / f"{symbol}_{timeframe}.csv"


def save_bars(path: Path, bars: Sequence[MarketBar]) -> None:
    """Overwrites `path` entirely with `bars`, sorted oldest-first. Raises ValueError if `bars`
    is empty or mixes more than one symbol/timeframe -- a real correctness bug this can catch
    for free (a caller accidentally writing mixed data into one file), not just defensive
    padding; every real caller only ever has one symbol/timeframe in hand at a time anyway."""
    if not bars:
        raise ValueError("save_bars() requires at least one bar -- refusing to write an empty cache")
    symbols = {b.symbol for b in bars}
    timeframes = {b.timeframe for b in bars}
    if len(symbols) != 1 or len(timeframes) != 1:
        raise ValueError(
            f"save_bars() requires all bars to share one symbol and one timeframe, got "
            f"symbols={symbols} timeframes={timeframes}"
        )

    ordered = sorted(bars, key=lambda b: b.time)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for bar in ordered:
            writer.writerow({
                "time": bar.time.isoformat(), "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "tick_volume": bar.tick_volume,
                "spread": bar.spread,
            })


def load_bars(path: Path, symbol: str, timeframe: str) -> list[MarketBar]:
    """Returns [] if `path` doesn't exist yet -- see module docstring. `symbol`/`timeframe` are
    supplied by the caller (not read from the file) since they're not stored per-row; callers
    are expected to pass the same values used to build `path` via cache_path()."""
    if not path.exists():
        return []
    bars: list[MarketBar] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(MarketBar(
                symbol=symbol, timeframe=timeframe, time=datetime.fromisoformat(row["time"]),
                open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                close=float(row["close"]), tick_volume=int(row["tick_volume"]),
                spread=int(row["spread"]),
            ))
    bars.sort(key=lambda b: b.time)
    return bars


def merge_bars(existing: Sequence[MarketBar], new: Sequence[MarketBar]) -> list[MarketBar]:
    """Pure combine: dedups by `.time` (a `new` bar overwrites an `existing` one at the same
    timestamp -- the safer default for a re-fetch, even though a fully-closed historical bar
    shouldn't normally change), returns sorted oldest-first. The building block for extending an
    already-seeded cache later without re-downloading everything -- callers do
    `save_bars(path, merge_bars(load_bars(path, ...), freshly_fetched))`."""
    by_time: dict[datetime, MarketBar] = {b.time: b for b in existing}
    by_time.update({b.time: b for b in new})
    return sorted(by_time.values(), key=lambda b: b.time)
