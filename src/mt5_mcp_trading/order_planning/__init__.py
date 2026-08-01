"""SizedIntent + RiskDecision -> OrderPlan. No adapter imports, ever (enforced by
tests/test_architecture.py) — this package normalizes against SymbolInfo (digits, point,
volume step, stops/freeze level) but never talks to MT5/MCP itself; SymbolInfo is passed in
as plain data. Intentionally empty in Phase 2; populated in Phase 5."""
