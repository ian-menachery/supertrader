"""YAML config loader with `extends:` inheritance.

A run is fully specified by one YAML file. That file may declare
`extends: <relative-path>` to inherit from a parent (which may itself extend).
Inheritance is resolved by deep-merging: dicts are merged recursively, lists
are *replaced* (not concatenated), scalars are overwritten. Child always wins
on conflicts.

The merged dict is validated through `config.schemas.RunConfig` — any Pydantic
violation surfaces with the originating file path attached.

Cycles raise `ConfigCycleError` with the full chain.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from supertrader.config.schemas import RunConfig


class ConfigCycleError(ValueError):
    """Raised when `extends:` chain visits the same file twice."""


def deep_merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Merge `child` into `parent`. Dicts recurse, everything else is replaced."""
    out: dict[str, Any] = dict(parent)
    for k, v in child.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        msg = f"Top-level YAML at {path} must be a mapping, got {type(raw).__name__}"
        raise TypeError(msg)
    return raw


def _resolve_extends_chain(path: Path, visited: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Recursively resolve `extends:` references. Returns the fully merged dict."""
    resolved = path.resolve()
    if resolved in visited:
        chain = " -> ".join(str(p) for p in (*visited, resolved))
        msg = f"Config inheritance cycle: {chain}"
        raise ConfigCycleError(msg)

    raw = _load_yaml(resolved)
    extends = raw.pop("extends", None)
    if extends is None:
        return raw

    extends_path = Path(extends)
    parent_path = (
        extends_path if extends_path.is_absolute() else (resolved.parent / extends_path)
    ).resolve()
    if not parent_path.exists():
        msg = f"Config {resolved} extends '{extends}', but {parent_path} does not exist."
        raise FileNotFoundError(msg)

    parent = _resolve_extends_chain(parent_path, (*visited, resolved))
    return deep_merge(parent, raw)


def load_run_config(path: Path | str) -> RunConfig:
    """Load a YAML run config from disk and return a validated `RunConfig`.

    Raises:
        FileNotFoundError: the YAML or its extends target doesn't exist.
        ConfigCycleError: the extends chain is cyclic.
        TypeError: the top-level YAML is not a mapping.
        pydantic.ValidationError: the merged dict fails schema validation.

    """
    p = Path(path)
    if not p.exists():
        msg = f"Run config not found at {p}"
        raise FileNotFoundError(msg)
    merged = _resolve_extends_chain(p)
    return RunConfig(**merged)
