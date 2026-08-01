"""Symbol-level and portfolio-level risk guards, producing RiskDecision. No adapter imports,
ever (enforced by tests/test_architecture.py). No guard here may ever be skippable by another
guard passing.

symbol_guards.py (duplicate-order prevention) and portfolio_guards.py (exposure caps) ported
in Phase 4's wiring step -- see each module's docstring for what was preserved vs. deliberately
strengthened. Each guard is independent and composable; nothing here yet decides how multiple
guards combine into one accept/reject outcome for a real pipeline run -- that's a later step.
Margin guards, spread filters, and daily shutdown rules do not exist in the legacy project
(confirmed absent in the Phase 0 audit) and are not ported here since there's nothing to port;
they'd be new functionality, not a migration.
"""
