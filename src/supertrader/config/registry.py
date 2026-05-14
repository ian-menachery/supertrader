"""String -> class registries for each layer.

Concrete `DataSource`, `Signal`, `Strategy`, and `ExecutionAdapter` classes
register themselves under their canonical `type:` string. Configs reference
implementations by that string; the loader uses the registry to resolve them.

Registration happens at import time. The runtime imports concrete modules once
(typically via `supertrader.bootstrap`) so all registrations are present when
`config.loader.load_run_config` resolves types.
"""

from __future__ import annotations

from collections.abc import Callable


class Registry[T]:
    """A string -> class lookup for one architectural layer.

    Contract:
      * Keys are non-empty strings; keys are unique within a registry.
      * `register(key)` is used as a class decorator; it returns the class
        unchanged so the decorator stack stays predictable.
      * `resolve(key)` raises `KeyError` with the list of available keys when
        a lookup misses — the error message is the public debug surface for
        config typos.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._items: dict[str, type[T]] = {}

    def register(self, key: str) -> Callable[[type[T]], type[T]]:
        if not isinstance(key, str) or not key:
            msg = f"Registry key must be a non-empty string, got {key!r}"
            raise ValueError(msg)

        def decorator(cls: type[T]) -> type[T]:
            if key in self._items:
                existing = self._items[key]
                msg = (
                    f"{self.name}: '{key}' already registered to "
                    f"{existing.__module__}.{existing.__name__}"
                )
                raise ValueError(msg)
            self._items[key] = cls
            return cls

        return decorator

    def resolve(self, key: str) -> type[T]:
        if key not in self._items:
            available = ", ".join(sorted(self._items.keys())) or "<empty>"
            msg = f"{self.name} has no entry for '{key}'. Available: {available}"
            raise KeyError(msg)
        return self._items[key]

    def keys(self) -> list[str]:
        return sorted(self._items.keys())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._items

    def __len__(self) -> int:
        return len(self._items)


# Module-level registries — one per architectural layer.
# Concrete classes register on import; the loader resolves at config-load time.
data_sources: Registry[object] = Registry("data_sources")
signals: Registry[object] = Registry("signals")
strategies: Registry[object] = Registry("strategies")
execution_adapters: Registry[object] = Registry("execution_adapters")
