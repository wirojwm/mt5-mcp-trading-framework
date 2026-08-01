"""TradeIntent + account/market context -> lot size. No adapter imports, ever (enforced by
tests/test_architecture.py).

money.py ported in Phase 4 (see its module docstring for exactly what was and wasn't
preserved from the legacy project, including a real crash bug that was found and fixed
rather than reproduced). Does not clamp against broker volume_min/max/step -- that is
order_planning's job, not yet migrated.
"""
