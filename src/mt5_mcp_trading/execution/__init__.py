"""OrderPlan -> ExecutionResult. The only package (besides mt5_adapter/mcp_adapter
themselves) allowed to hold an OrderExecutor reference, and the only place that decides which
concrete adapter (mock/dry-run/shadow/demo) is wired in for a given run, based on
config.settings.ExecutionMode. Every result is verified against actual MT5 state before being
considered successful.

Intentionally empty in Phase 2 (no adapters are wired to anything real yet). The dry-run
implementation is added in Phase 5; the demo executor, gated by require_demo_account and
explicit approval, is added in Phase 6.
"""
