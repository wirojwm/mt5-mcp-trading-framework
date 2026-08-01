"""
Async orchestration: ties market_data + mt5_adapter (AccountReader) together with
strategy/trade_intent/sizing/risk/order_planning/execution for one symbol, one cycle, at a
time. Not in the Phase 1 design's original package list -- added here because nothing else
had this job. It is deliberately NOT a pure package (unlike strategy/signal/trade_intent/
sizing/risk/order_planning): it awaits MarketDataSource and AccountReader calls and holds an
OrderExecutor reference, so tests/test_architecture.py does not (and should not) require it to
avoid adapter imports the way it does for the pure packages.

Each function here takes its MarketDataSource/AccountReader/OrderExecutor as plain
constructor-injected arguments -- callable with Phase 2's mocks (as tests/integration/
test_*_dry_run_pipeline.py do) or with real adapters once mt5_adapter/mcp_adapter have real
implementations, with no code change here required either way. That's the whole point of the
interfaces existing.
"""
