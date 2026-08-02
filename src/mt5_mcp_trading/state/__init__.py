"""Persisted run state — a record of what happened, never the source of a trading decision.
The legacy project had no persistence at all (in-memory dicts lost on kernel restart); this
package exists specifically to fix that gap.

Populated for Phase 6 (controlled demo execution): models.py (LocalOrderRecord,
ReconciliationReport), store.py (StateStore, atomic JSON persistence), reconcile.py (pure
ticket-only cross-check against real MT5 state), policy.py (ExecutionPosture: NORMAL/
MANAGE_ONLY/BLOCKED), strategy_registry.py (explicit magic->strategy-name mapping). Exists
because metatrader-mcp-server's trading tools silently drop the magic number on every real
order (see docs/mcp_tool_classification.md, Known Issues item 7) -- this package is what lets
mt5_adapter.mcp_order_executor.McpOrderExecutor still know which strategy a real position
belongs to, without ever guessing from symbol/timing.
"""
