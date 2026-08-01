"""MT5 account-state and order-execution interfaces, plus the demo-account safety guard.

This package (and mcp_adapter) are the only ones allowed to be imported by execution/.
strategy/, signal/, trade_intent/, sizing/, risk/, and order_planning/ must never import this
package — enforced by tests/test_architecture.py.

mcp_market_data.py (McpMarketDataSource) and mcp_account.py (McpAccountReader): real,
MCP-backed implementations. No OrderExecutor implementation exists yet (that's the trading-
capable interface; still Phase 6 work, gated by explicit approval). Both real implementations
have confirmed, documented gaps versus what the interfaces promise -- no SymbolInfo support,
no reliable trade_mode, no magic-number filtering -- see each module's docstring. These are
gaps in metatrader-mcp-server's read-only tool surface, not oversights in this adapter code.
"""
