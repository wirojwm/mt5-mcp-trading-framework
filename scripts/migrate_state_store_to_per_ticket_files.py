#!/usr/bin/env python3
"""
One-time migration: converts a StateStore from the old single-file format
(`var/order_state.json`, one JSON blob holding every ticket under a "records" key) to the new
per-ticket-file format (`var/order_state/<ticket>.json`, one file per ticket) -- see
src/mt5_mcp_trading/state/store.py's module docstring for why (a real O(N^2)-for-N-writes
scaling problem found in Phase 7, docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md).

`StateStore` itself does NOT read the old format -- this script is the explicit, one-time
bridge. Pure file conversion, no MCP/MT5 call, no dependency on execution mode.

Safety: the source file is never deleted. On success it's renamed to
`<source>.migrated-bak` (kept as an audit-trail backup) after every record round-trips through
the new StateStore correctly. Refuses to run if the destination directory already exists and is
non-empty, to avoid silently merging into or overwriting an unrelated store.

Usage:
    .venv/Scripts/python.exe scripts/migrate_state_store_to_per_ticket_files.py \\
        [--source var/order_state.json] [--dest var/order_state]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mt5_mcp_trading.state.store import StateStore, _deserialize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT / "var" / "order_state.json")
    parser.add_argument("--dest", type=Path, default=PROJECT_ROOT / "var" / "order_state")
    args = parser.parse_args()

    source: Path = args.source
    dest: Path = args.dest

    if not source.exists():
        print(f"Source file {source} does not exist -- nothing to migrate.")
        return 0

    if dest.exists() and any(dest.iterdir()):
        print(f"Destination directory {dest} already exists and is non-empty -- refusing to "
              "migrate into it. Move it aside first if this is intentional.")
        return 1

    raw = json.loads(source.read_text(encoding="utf-8"))
    records = {
        int(ticket): _deserialize(int(ticket), data)
        for ticket, data in raw["records"].items()
    }
    print(f"Read {len(records)} record(s) from {source}.")

    store = StateStore(dest)
    for ticket, record in records.items():
        store._write_one(record)  # noqa: SLF001 -- this script IS the one-time bridge

    # Verify every record round-trips exactly before touching the source file.
    for ticket, expected in records.items():
        actual = store.lookup(ticket)
        if actual != expected:
            print(f"MISMATCH after migration for ticket={ticket} -- source file left untouched, "
                  f"destination NOT renamed away. Investigate before retrying.")
            return 1

    backup = source.with_name(source.name + ".migrated-bak")
    source.rename(backup)
    print(f"Migrated {len(records)} record(s) to {dest}. Source backed up to {backup}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
