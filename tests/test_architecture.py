"""
Enforces the pure-core / adapter boundary from AGENTS.md structurally, not just by
documentation: strategy, signal, trade_intent, sizing, risk, and order_planning must never
import mt5_adapter, mcp_adapter, or execution — directly or via any submodule.

This uses `ast` to parse import statements rather than actually importing the packages, so it
has zero risk of triggering any adapter/network code as a side effect of running the test
suite.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
PACKAGE_ROOT = SRC_ROOT / "mt5_mcp_trading"

# The pure decision-making core: must never depend on how (or whether) anything is executed.
PURE_PACKAGES = [
    "strategy",
    "signal",
    "trade_intent",
    "sizing",
    "risk",
    "order_planning",
]

FORBIDDEN_MODULE_PREFIXES = (
    "mt5_mcp_trading.mt5_adapter",
    "mt5_mcp_trading.mcp_adapter",
    "mt5_mcp_trading.execution",
    "mt5_adapter",
    "mcp_adapter",
    "execution",
)


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _python_files(package: str) -> list[Path]:
    pkg_dir = PACKAGE_ROOT / package
    if not pkg_dir.exists():
        return []
    return sorted(pkg_dir.rglob("*.py"))


@pytest.mark.parametrize("package", PURE_PACKAGES)
def test_pure_package_exists(package: str) -> None:
    assert (PACKAGE_ROOT / package).exists(), (
        f"Expected package src/mt5_mcp_trading/{package}/ to exist per the approved "
        f"architecture, even if currently empty."
    )


@pytest.mark.parametrize("package", PURE_PACKAGES)
def test_pure_package_has_no_adapter_imports(package: str) -> None:
    for file in _python_files(package):
        for imported in _imported_module_names(file):
            assert not imported.startswith(FORBIDDEN_MODULE_PREFIXES), (
                f"{file.relative_to(SRC_ROOT.parent)} imports {imported!r}, which violates "
                f"the pure-core / adapter boundary (see AGENTS.md)."
            )


def test_only_mt5_adapter_and_mcp_adapter_may_import_mcp_adapter() -> None:
    """mcp_adapter itself, mt5_adapter, and execution may import mcp_adapter. Nothing else may."""
    allowed_importers = {"mcp_adapter", "mt5_adapter", "execution"}
    for package_dir in PACKAGE_ROOT.iterdir():
        if not package_dir.is_dir() or package_dir.name in allowed_importers:
            continue
        for file in sorted(package_dir.rglob("*.py")):
            for imported in _imported_module_names(file):
                assert not imported.startswith(("mt5_mcp_trading.mcp_adapter", "mcp_adapter")), (
                    f"{file.relative_to(SRC_ROOT.parent)} imports {imported!r}, but only "
                    f"mt5_adapter/execution/mcp_adapter itself may import mcp_adapter."
                )
