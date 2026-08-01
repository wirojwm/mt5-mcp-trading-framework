"""The only package allowed to import an MCP client library or hold an MCP connection.

Imported only by mt5_adapter/ (and, transitively, execution/). Never imported by strategy/,
signal/, trade_intent/, sizing/, risk/, or order_planning/ — enforced by
tests/test_architecture.py.

client.py: generic MCP stdio client, enforcing tool_registry.py's gate on every call.
metatrader_tools.py: the real, live-verified tool classification for metatrader-mcp-server
(added when the real mt5_adapter was wired in) -- see its module docstring and
docs/mcp_tool_classification.md for what's actually exposed and what isn't.
"""
