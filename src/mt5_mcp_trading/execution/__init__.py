"""OrderPlan -> ExecutionResult. The only package (besides mt5_adapter/mcp_adapter
themselves) allowed to hold an OrderExecutor reference, and the only place that decides which
concrete adapter (mock/dry-run/shadow/demo) is wired in for a given run, based on
config.settings.ExecutionMode. Every result is verified against actual MT5 state before being
considered successful.

dry_run.py (DryRunExecutor) added in Phase 5, as promised in Phase 2's docstring here.

composition.py (demo_execution_session), added for Phase 6: the one place
ToolRegistry(trading_enabled=True) -- and therefore a real, trading-capable
mt5_adapter.mcp_order_executor.McpOrderExecutor -- is ever constructed outside a test, gated
by require_demo_account_kind (hard) and require_demo_account (informational, see
mt5_adapter/safety.py's module docstring for why). No script wires trading_enabled=True by
hand; everything goes through this one function.
"""
