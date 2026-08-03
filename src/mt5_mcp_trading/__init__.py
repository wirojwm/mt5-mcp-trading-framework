"""mt5-mcp-trading: clean-architecture MT5 trading system built around an isolated MCP adapter.

See AGENTS.md at the project root for safety rules and the phase workflow this project follows.
"""

import sys

# Hard gate, not advisory: metatrader-mcp-server==0.5.1 requires Python >=3.10, and this
# project standardizes on exactly 3.12 across both development machines (see
# .python-version, pyproject.toml requires-python, README.md Setup). Fail at import time
# with a clear message rather than letting an old interpreter fail later with a confusing
# dependency or syntax error.
if sys.version_info < (3, 12):
    raise RuntimeError(
        "mt5_mcp_trading requires Python >=3.12 (found "
        f"{sys.version_info.major}.{sys.version_info.minor}). "
        "Install Python 3.12 64-bit and recreate .venv -- see README.md Setup."
    )
