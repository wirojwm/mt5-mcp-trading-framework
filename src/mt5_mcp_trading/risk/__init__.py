"""Symbol-level and portfolio-level risk guards, producing RiskDecision. No adapter imports,
ever (enforced by tests/test_architecture.py). No guard here may ever be skippable by another
guard passing. Intentionally empty in Phase 2; populated in Phase 5 (dry-run pipeline needs
real guards to validate against, even before any execution is possible)."""
