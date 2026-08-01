"""Pure strategy functions: bars/features -> Signal or strategy-specific output (e.g.
GridLevels). No adapter imports, ever (enforced by tests/test_architecture.py).

grid.py ported in Phase 4 (see its module docstring for exactly what was and wasn't
preserved from the legacy project). Runner (MACD/EMA crossover) and guard trigger logic are
not yet ported.
"""
