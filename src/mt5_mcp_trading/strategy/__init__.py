"""Pure strategy functions: bars/features -> Signal or strategy-specific output (e.g.
GridLevels, GuardAction). No adapter imports, ever (enforced by tests/test_architecture.py).

grid.py, runner.py, and guard.py all ported in Phase 4 (see each module's docstring for
exactly what was and wasn't preserved from the legacy project, including two real bugs found
and fixed in guard.py rather than reproduced).
"""
