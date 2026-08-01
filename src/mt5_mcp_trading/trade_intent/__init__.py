"""Signal/GridLevels -> TradeIntent assembly. No adapter imports, ever (enforced by
tests/test_architecture.py).

grid.py populated in the grid end-to-end wiring step: grid always proposes both a BUY_LIMIT
and a SELL_LIMIT TradeIntent per evaluation, deferring to risk/ to decide which (if either)
survive. Runner's Signal -> TradeIntent assembly is not yet built.
"""
