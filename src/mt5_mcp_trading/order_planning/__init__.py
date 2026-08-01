"""SizedIntent + RiskDecision -> OrderPlan. No adapter imports, ever (enforced by
tests/test_architecture.py) -- this package normalizes against SymbolInfo (point, volume
step, stops/freeze level) and Tick (bid/ask), both passed in as plain data, never talking to
MT5/MCP itself.

limit_price.py (price normalization) ported from mt5_bridge.normalize_price() in Phase 4's
wiring step. plan.py (build_order_plan, volume clamping) is new functionality, not a
migration -- see plan.py's module docstring for why (the legacy volume-clamping hook was
always a no-op).
"""
