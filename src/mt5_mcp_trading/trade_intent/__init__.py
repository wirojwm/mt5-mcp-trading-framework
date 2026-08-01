"""Signal/GridLevels -> TradeIntent assembly. No adapter imports, ever (enforced by
tests/test_architecture.py).

grid.py: grid always proposes both a BUY_LIMIT and a SELL_LIMIT TradeIntent per evaluation,
deferring to risk/ to decide which (if either) survive.

runner.py: LONG -> BUY, SHORT -> SELL (both MARKET), FLAT -> None (no intent at all). See its
module docstring for why FLAT has no defined action -- the legacy project never gave it one.
"""
