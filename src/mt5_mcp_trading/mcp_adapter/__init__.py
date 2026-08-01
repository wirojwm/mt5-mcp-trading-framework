"""The only package allowed to import an MCP client library or hold an MCP connection.

Imported only by mt5_adapter/ (and, transitively, execution/). Never imported by strategy/,
signal/, trade_intent/, sizing/, risk/, or order_planning/ — enforced by
tests/test_architecture.py.
"""
