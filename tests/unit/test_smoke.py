"""Smoke tests: the package imports and the version string is present."""

from __future__ import annotations

import supertrader


def test_package_imports() -> None:
    assert supertrader.__version__


def test_version_is_semver_ish() -> None:
    parts = supertrader.__version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])
