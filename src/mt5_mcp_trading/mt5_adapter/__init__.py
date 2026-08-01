"""MT5 account-state and order-execution interfaces, plus the demo-account safety guard.

This package (and mcp_adapter) are the only ones allowed to be imported by execution/.
strategy/, signal/, trade_intent/, sizing/, risk/, and order_planning/ must never import this
package — enforced by tests/test_architecture.py.
"""
