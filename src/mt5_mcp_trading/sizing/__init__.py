"""TradeIntent + AccountState -> SizedIntent. No adapter imports, ever (enforced by
tests/test_architecture.py). Intentionally empty in Phase 2; populated in Phase 4/5 by porting
the reusable lot-sizing formulas identified in the Phase 0 legacy audit
(money_management.MoneyConfig.decide_lot) as tested pure functions."""
