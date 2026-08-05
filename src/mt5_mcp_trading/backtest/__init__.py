"""
Phase 8 (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): offline research/backtest tooling. Not
part of the live pipeline (`market_data -> features -> strategy -> signal -> trade_intent ->
sizing -> risk -> order_planning -> execution -> mt5_adapter -> mcp_adapter`, AGENTS.md
"Architecture boundaries") and not enforced by tests/test_architecture.py's PURE_PACKAGES list,
which is specifically about that documented live pipeline -- but written to the same discipline
voluntarily: no mt5_adapter/mcp_adapter/execution imports anywhere in this package. Real MCP
calls to seed the local cache this package reads/writes happen only in scripts/, never here,
same separation of concerns as every other adapter-touching action in this project.
"""
