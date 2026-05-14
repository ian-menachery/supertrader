"""Unit tests for the RunManifest model and helpers."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from supertrader.observability.run_manifest import (
    RunRefusedError,
    config_hash,
    git_state,
    hash_input_partitions,
    manifest_from_row,
    manifest_to_row,
    python_version_short,
    start_manifest,
)


def _git(repo: Path, *args: str) -> None:
    """Run a git command in repo, silently asserting success."""
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def test_python_version_short_format() -> None:
    s = python_version_short()
    assert s.count(".") == 2
    parts = s.split(".")
    assert all(p.isdigit() for p in parts)


def test_git_state_clean(tmp_git_repo: Path) -> None:
    sha, dirty = git_state(tmp_git_repo)
    assert len(sha) == 40
    assert dirty is False


def test_git_state_dirty(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    sha, dirty = git_state(tmp_git_repo)
    assert len(sha) == 40
    assert dirty is True


def test_git_state_handles_git_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the `git` binary is missing, git_state returns ('unknown', False)."""

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sha, dirty = git_state(tmp_path)
    assert sha == "unknown"
    assert dirty is False


def test_config_hash_deterministic() -> None:
    """Two calls with the same config json must produce identical hashes."""

    class Stub:
        def model_dump_json(self) -> str:
            return '{"a": 1, "b": 2}'

    h1 = config_hash(Stub())  # type: ignore[arg-type]
    h2 = config_hash(Stub())  # type: ignore[arg-type]
    assert h1 == h2
    assert len(h1) == 32  # 16 bytes hex


def test_config_hash_changes_on_content() -> None:
    class StubA:
        def model_dump_json(self) -> str:
            return '{"a": 1}'

    class StubB:
        def model_dump_json(self) -> str:
            return '{"a": 2}'

    assert config_hash(StubA()) != config_hash(StubB())  # type: ignore[arg-type]


def test_start_manifest_clean_repo(tmp_git_repo: Path) -> None:
    m = start_manifest(
        run_id="run-1",
        config_path=Path("configs/runs/x.yaml"),
        config_hash_hex="deadbeef" * 4,
        repo_root=tmp_git_repo,
    )
    assert m.run_id == "run-1"
    assert m.git_dirty is False
    assert m.status == "running"
    assert m.ended_at is None
    assert len(m.git_sha) == 40
    assert m.python_version == python_version_short()


def test_start_manifest_refuses_dirty(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RunRefusedError, match="dirty"):
        start_manifest(
            run_id="run-1",
            config_path=Path("x.yaml"),
            config_hash_hex="a" * 32,
            repo_root=tmp_git_repo,
        )


def test_start_manifest_allow_dirty_records_flag(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    m = start_manifest(
        run_id="run-1",
        config_path=Path("x.yaml"),
        config_hash_hex="a" * 32,
        repo_root=tmp_git_repo,
        allow_dirty=True,
    )
    assert m.git_dirty is True


def test_with_status_returns_finalized_copy(tmp_git_repo: Path) -> None:
    started = start_manifest(
        run_id="r",
        config_path=Path("x.yaml"),
        config_hash_hex="a" * 32,
        repo_root=tmp_git_repo,
    )
    ended_at = datetime.now(tz=UTC)
    finalized = started.with_status(status="ok", ended_at=ended_at, data_hashes={"foo": "bar"})
    assert finalized.status == "ok"
    assert finalized.ended_at == ended_at
    assert finalized.data_hashes == {"foo": "bar"}
    # Original unchanged
    assert started.status == "running"
    assert started.ended_at is None


def test_write_json_roundtrip(tmp_git_repo: Path, tmp_path: Path) -> None:
    m = start_manifest(
        run_id="r",
        config_path=Path("x.yaml"),
        config_hash_hex="a" * 32,
        repo_root=tmp_git_repo,
    )
    out = tmp_path / "manifest.json"
    m.write_json(out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["run_id"] == "r"
    assert parsed["status"] == "running"


def test_manifest_to_row_and_back(tmp_git_repo: Path) -> None:
    m = start_manifest(
        run_id="r",
        config_path=Path("x.yaml"),
        config_hash_hex="a" * 32,
        repo_root=tmp_git_repo,
    )
    finalized = m.with_status(
        status="ok",
        ended_at=datetime.now(tz=UTC),
        data_hashes={"yfinance/prices/daily/ticker=AAPL/data.parquet": "abc"},
    )
    row = manifest_to_row(finalized)
    rebuilt = manifest_from_row(row)
    assert rebuilt.run_id == finalized.run_id
    assert rebuilt.config_hash == finalized.config_hash
    assert rebuilt.git_sha == finalized.git_sha
    assert rebuilt.status == "ok"
    assert rebuilt.data_hashes == finalized.data_hashes


def test_hash_input_partitions_skips_missing(tmp_path: Path) -> None:
    from supertrader.data.store import ParquetStore

    store = ParquetStore(tmp_path)
    # No data written for either source — should silently skip both.
    out = hash_input_partitions(store.root, ["yfinance.prices.daily", "arctic_shift.posts"])
    assert out == {}


def test_hash_input_partitions_hashes_present_files(tmp_path: Path) -> None:
    import polars as pl

    from supertrader.data.store import ParquetStore

    store = ParquetStore(tmp_path)
    frame = pl.LazyFrame(
        {"ticker": ["AAPL", "MSFT"], "date": ["2024-01-01", "2024-01-02"], "close": [1.0, 2.0]}
    )
    store.write("yfinance.prices.daily", frame, partition_keys=("ticker",))
    out = hash_input_partitions(store.root, ["yfinance.prices.daily"])
    assert len(out) == 2
    for rel, h in out.items():
        assert rel.startswith("store/yfinance/prices/daily/ticker=")
        assert len(h) == 32  # 16-byte blake2b hex
