"""Layer-boundary tests — supplementary to import-linter.

`import-linter` enforces directional layer rules; this test catches two more
specific things:

1. Signal modules must not import concrete `DataSource` implementations or
   the writer side of `ParquetStore`. They consume `PointInTimeStore` only.
2. Strategy modules must not import any `data.*` symbols. They consume signals.

The check uses `ast` so we don't actually import the modules — fast, no
side effects, picks up dead imports as well.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "supertrader"


def _imports_from(path: Path) -> set[str]:
    """Return the set of fully-qualified modules imported from `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                out.add(f"{mod}.{alias.name}" if mod else alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
    return out


def _py_files(subdir: str) -> list[Path]:
    return [p for p in (SRC_ROOT / subdir).rglob("*.py") if "__pycache__" not in p.parts]


class TestSignalLayerHygiene:
    @pytest.mark.parametrize("py_file", _py_files("signals"), ids=lambda p: p.name)
    def test_no_concrete_sources_or_writer(self, py_file: Path) -> None:
        imports = _imports_from(py_file)
        violations = sorted(
            i
            for i in imports
            if i.startswith("supertrader.data.sources")
            or i == "supertrader.data.store.ParquetStore"
        )
        assert not violations, (
            f"{py_file.relative_to(SRC_ROOT)} violates signal-layer hygiene: "
            f"imports {violations}. Signals must consume only `PointInTimeStore` and "
            f"`Signal`/`SentimentScorer` ABCs."
        )


class TestStrategyLayerHygiene:
    @pytest.mark.parametrize("py_file", _py_files("strategies"), ids=lambda p: p.name)
    def test_no_data_layer_imports(self, py_file: Path) -> None:
        imports = _imports_from(py_file)
        violations = sorted(i for i in imports if i.startswith("supertrader.data"))
        assert not violations, (
            f"{py_file.relative_to(SRC_ROOT)} violates strategy-layer hygiene: "
            f"imports {violations}. Strategies consume signals, not raw data."
        )


class TestExecutionLayerHygiene:
    @pytest.mark.parametrize("py_file", _py_files("execution"), ids=lambda p: p.name)
    def test_no_signals_or_data_layer(self, py_file: Path) -> None:
        imports = _imports_from(py_file)
        violations = sorted(
            i for i in imports if i.startswith(("supertrader.signals", "supertrader.data.sources"))
        )
        assert not violations, (
            f"{py_file.relative_to(SRC_ROOT)} violates execution-layer hygiene: "
            f"imports {violations}. Execution translates target weights to orders only."
        )
