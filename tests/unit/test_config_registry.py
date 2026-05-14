"""Tests for `config.registry.Registry` and the module-level registries."""

from __future__ import annotations

import pytest

from supertrader.config.registry import Registry


class _A:
    pass


class _B:
    pass


class TestRegistry:
    def test_register_and_resolve(self) -> None:
        reg: Registry[object] = Registry("test")
        reg.register("a")(_A)
        assert reg.resolve("a") is _A
        assert "a" in reg
        assert len(reg) == 1

    def test_decorator_returns_class_unchanged(self) -> None:
        reg: Registry[object] = Registry("test")
        result = reg.register("a")(_A)
        assert result is _A

    def test_duplicate_key_raises(self) -> None:
        reg: Registry[object] = Registry("test")
        reg.register("a")(_A)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("a")(_B)

    def test_empty_key_raises(self) -> None:
        reg: Registry[object] = Registry("test")
        with pytest.raises(ValueError, match="non-empty string"):
            reg.register("")

    def test_resolve_unknown_lists_available(self) -> None:
        reg: Registry[object] = Registry("test")
        reg.register("alpha")(_A)
        reg.register("beta")(_B)
        with pytest.raises(KeyError, match="alpha, beta"):
            reg.resolve("gamma")

    def test_resolve_unknown_empty_registry_message(self) -> None:
        reg: Registry[object] = Registry("test")
        with pytest.raises(KeyError, match="<empty>"):
            reg.resolve("missing")

    def test_keys_returns_sorted(self) -> None:
        reg: Registry[object] = Registry("test")
        reg.register("zeta")(_A)
        reg.register("alpha")(_B)
        assert reg.keys() == ["alpha", "zeta"]

    def test_contains_rejects_non_string(self) -> None:
        reg: Registry[object] = Registry("test")
        reg.register("a")(_A)
        assert 42 not in reg
