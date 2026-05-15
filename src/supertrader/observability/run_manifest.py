"""Run manifest: the reproducibility ledger for every backtest run.

A manifest captures everything needed to answer "what did this run *actually*
see?" — the validated config hash, the git SHA + dirty flag, the Python and
supertrader versions, the bookend timestamps, the final status, and a
content-hash for every input partition that the run could have read.

Two persistence sinks per run:
  * SQLite row in `run_manifests` (queryable history across runs).
  * Filesystem mirror `data/runs/<run_id>/manifest.json` (git-able provenance
    next to the tear sheet).

By default `run_backtest()` refuses to run on a dirty git tree. The escape
hatch is the CLI flag `--allow-dirty`; even then `git_dirty=True` is recorded
on the manifest so reproducibility claims are not silently false later.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from supertrader import _version as _supertrader_version


class _SupportsModelDumpJson(Protocol):
    """Anything with `.model_dump_json()` (e.g., any pydantic BaseModel)."""

    def model_dump_json(self) -> str: ...


log = logging.getLogger(__name__)

RunStatus = Literal["running", "ok", "failed"]


class RunRefusedError(RuntimeError):
    """Raised when a precondition for running blocks the run (e.g. dirty git)."""


class RunManifest(BaseModel):
    """Reproducibility ledger for one backtest run."""

    model_config = ConfigDict(frozen=False)

    run_id: str
    config_path: Path
    config_hash: str
    git_sha: str
    git_dirty: bool
    python_version: str
    supertrader_version: str
    started_at: datetime
    ended_at: datetime | None = None
    status: RunStatus
    data_hashes: dict[str, str] = Field(default_factory=dict)
    # Added per ADR 0012. Empty string for legacy rows that pre-date the
    # field; "static:<hash>" or "pit:<hash>" once the pipeline writes it.
    universe_snapshot_hash: str = ""

    def with_status(
        self,
        *,
        status: RunStatus,
        ended_at: datetime | None = None,
        data_hashes: dict[str, str] | None = None,
    ) -> RunManifest:
        """Return a copy with the bookend fields filled in."""
        update: dict[str, object] = {"status": status}
        if ended_at is not None:
            update["ended_at"] = ended_at
        if data_hashes is not None:
            update["data_hashes"] = data_hashes
        return self.model_copy(update=update)

    def write_json(self, path: Path) -> None:
        """Mirror the manifest to a JSON file (used by the tear sheet header)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


def config_hash(config: _SupportsModelDumpJson) -> str:
    """Stable Blake2b hash of a validated config's JSON form.

    Used as the cache + holdout-guard key. Owned by the manifest module so the
    pipeline imports it from one place. Accepts any object with
    `.model_dump_json()` (every pydantic BaseModel qualifies) to avoid pulling
    `supertrader.config.schemas` into the observability layer.
    """
    blob = config.model_dump_json()
    return hashlib.blake2b(blob.encode(), digest_size=16).hexdigest()


def git_state(repo_root: Path) -> tuple[str, bool]:
    """Return `(git_sha, is_dirty)` for the repo containing `repo_root`.

    `is_dirty` is True if `git status --porcelain` returns any lines (modified,
    untracked, or staged). Returns `("unknown", False)` and warns if the path
    is not inside a git repository or git is unavailable.
    """
    try:
        sha_args = ["git", "-C", str(repo_root), "rev-parse", "HEAD"]
        status_args = ["git", "-C", str(repo_root), "status", "--porcelain"]
        sha_proc = subprocess.run(sha_args, capture_output=True, check=True, text=True)  # noqa: S603
        status_proc = subprocess.run(status_args, capture_output=True, check=True, text=True)  # noqa: S603
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.warning(
            "git unavailable or %s is not a git repo; recording git_sha=unknown",
            repo_root,
        )
        return ("unknown", False)
    sha = sha_proc.stdout.strip()
    is_dirty = bool(status_proc.stdout.strip())
    return (sha, is_dirty)


def python_version_short() -> str:
    """Return the short Python version string, e.g. `'3.12.5'`."""
    return ".".join(str(v) for v in sys.version_info[:3])


def _blake2b_file(path: Path) -> str:
    """16-byte Blake2b hex of a file. Duplicated locally to keep observability layer-clean."""
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_input_partitions(store_root: Path, source_ids: list[str]) -> dict[str, str]:
    """Hash every `data.parquet` under each source's store root.

    `store_root` is the on-disk root that `ParquetStore` was initialized with;
    the function only takes a path so this module avoids importing from the
    `data` layer.

    Returns a dict mapping a store-root-relative posix path to the 16-byte
    Blake2b hex of that file. Missing sources are skipped with a warning.
    """
    out: dict[str, str] = {}
    for source_id in source_ids:
        source_root = store_root / "store" / source_id.replace(".", "/")
        if not source_root.exists():
            log.warning(
                "source '%s' has no data on disk at %s; skipping hash",
                source_id,
                source_root,
            )
            continue
        for parquet in sorted(source_root.rglob("data.parquet")):
            rel = parquet.relative_to(store_root).as_posix()
            out[rel] = _blake2b_file(parquet)
    return out


def start_manifest(
    *,
    run_id: str,
    config_path: Path,
    config_hash_hex: str,
    repo_root: Path,
    allow_dirty: bool = False,
) -> RunManifest:
    """Build the at-start RunManifest, enforcing the dirty-git refusal."""
    git_sha, git_dirty = git_state(repo_root)
    if git_dirty and not allow_dirty:
        msg = (
            "git tree is dirty; refusing to run a manifest-recorded backtest. "
            "Commit, stash, or rerun with --allow-dirty (the dirty flag will "
            "be recorded on the manifest)."
        )
        raise RunRefusedError(msg)
    return RunManifest(
        run_id=run_id,
        config_path=config_path,
        config_hash=config_hash_hex,
        git_sha=git_sha,
        git_dirty=git_dirty,
        python_version=python_version_short(),
        supertrader_version=_supertrader_version.__version__,
        started_at=datetime.now(tz=UTC),
        ended_at=None,
        status="running",
        data_hashes={},
    )


ManifestRow = tuple[str, str, str, str, str, str, str | None, str, str, str]


def manifest_to_row(manifest: RunManifest) -> ManifestRow:
    """Flatten a RunManifest into the column tuple expected by `run_manifests`.

    Tuple shape mirrors the sqlite columns (see `data/store.py:SCHEMA_SQL`).
    The trailing `universe_snapshot_hash` was added per ADR 0012; legacy
    manifests carry an empty string.
    """
    return (
        manifest.run_id,
        str(manifest.config_path),
        manifest.config_hash,
        manifest.git_sha,
        manifest.python_version,
        manifest.started_at.isoformat(),
        manifest.ended_at.isoformat() if manifest.ended_at is not None else None,
        manifest.status,
        json.dumps(manifest.data_hashes, sort_keys=True),
        manifest.universe_snapshot_hash,
    )


def manifest_from_row(row: tuple[object, ...]) -> RunManifest:
    """Reconstruct a RunManifest from a `run_manifests` row.

    The on-disk schema (`data/store.py:SCHEMA_SQL`) does not include
    `git_dirty` or `supertrader_version`, so those round-trip via the JSON
    mirror only. SQLite roundtrip is good enough for `status`, timestamps,
    and `universe_snapshot_hash`; callers needing full fidelity should
    read the JSON mirror instead.

    Accepts 9-tuple legacy rows (pre-ADR-0012) by defaulting
    `universe_snapshot_hash` to an empty string.
    """
    universe_snapshot_hash: object
    if len(row) == 9:
        (
            run_id,
            config_path,
            config_hash_hex,
            git_sha,
            python_version,
            started_at,
            ended_at,
            status,
            data_hashes_json,
        ) = row
        universe_snapshot_hash = ""
    else:
        (
            run_id,
            config_path,
            config_hash_hex,
            git_sha,
            python_version,
            started_at,
            ended_at,
            status,
            data_hashes_json,
            universe_snapshot_hash,
        ) = row
    if status not in ("running", "ok", "failed"):
        msg = f"unrecognized status in run_manifests row: {status!r}"
        raise ValueError(msg)
    return RunManifest(
        run_id=str(run_id),
        config_path=Path(str(config_path)),
        config_hash=str(config_hash_hex),
        git_sha=str(git_sha),
        git_dirty=False,
        python_version=str(python_version),
        supertrader_version=_supertrader_version.__version__,
        started_at=datetime.fromisoformat(str(started_at)),
        ended_at=datetime.fromisoformat(str(ended_at)) if ended_at is not None else None,
        status=status,
        data_hashes=json.loads(str(data_hashes_json)) if data_hashes_json else {},
        universe_snapshot_hash=str(universe_snapshot_hash or ""),
    )
