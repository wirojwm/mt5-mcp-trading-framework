"""Persisted run state (what strategies are active, last known positions/orders) — a record
of what happened, never the source of a trading decision. The legacy project had no
persistence at all (in-memory dicts lost on kernel restart); this package exists specifically
to fix that gap. Intentionally empty in Phase 2; populated alongside Phase 5/6.
"""
