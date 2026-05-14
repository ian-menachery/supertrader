"""Integration tests for scripts/reset_holdout_lock.py.

Invokes the script as an in-process function call (faster than subprocess) and
verifies the meta-sqlite row deletion + log line append.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from supertrader.backtest.splits import HoldoutGuard, HoldoutOverrideLog
from supertrader.observability.run_manifest import RunRefusedError

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "reset_holdout_lock.py"


def _load_script_module() -> object:
    """Load `scripts/reset_holdout_lock.py` as a fresh module per call."""
    spec = importlib.util.spec_from_file_location("reset_holdout_lock", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def clean_repo_root(tmp_path: Path) -> Path:
    """Initialize a tmp git repo with /data gitignored. Returns the repo root.

    The real supertrader repo gitignores /data/, so the meta sqlite + override
    log live alongside the repo without being seen by `git status`. We mimic
    that here so the dirty-tree refusal doesn't fire spuriously.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    (repo / ".gitignore").write_text("/data/\n", encoding="utf-8")
    _git(repo, "add", "README.md", ".gitignore")
    _git(repo, "commit", "-m", "init")
    return repo


def test_reset_clears_lock_and_appends_log(
    clean_repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = clean_repo_root / "data"
    data_dir.mkdir()
    meta_db = data_dir / "meta.sqlite"

    # Seed the guard with one touch.
    guard = HoldoutGuard(meta_db)
    guard.evaluate(run_id="run-1", config_hash="hash-xyz")
    assert guard.has_touched("hash-xyz") is True

    # Run the script's main()
    mod = _load_script_module()
    monkeypatch.setattr(mod, "REPO_ROOT", clean_repo_root)
    monkeypatch.setattr(mod, "DEFAULT_DATA_DIR", data_dir)
    exit_code = mod.main(
        [
            "--config-hash",
            "hash-xyz",
            "--reason",
            "re-eval after fix",
            "--data-dir",
            str(data_dir),
            "--operator",
            "test-user",
        ]
    )
    assert exit_code == 0

    # Row should be gone.
    assert HoldoutGuard(meta_db).has_touched("hash-xyz") is False

    # Log line written.
    log = HoldoutOverrideLog(data_dir / "runs" / "holdout_overrides.log")
    records = log.read_all()
    assert len(records) == 1
    assert records[0]["config_hash"] == "hash-xyz"
    assert records[0]["reason"] == "re-eval after fix"
    assert records[0]["operator"] == "test-user"
    assert records[0]["original_run_id"] == "run-1"


def test_reset_refuses_on_dirty_tree(
    clean_repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (clean_repo_root / "untracked.txt").write_text("x", encoding="utf-8")
    data_dir = clean_repo_root / "data"
    data_dir.mkdir()
    HoldoutGuard(data_dir / "meta.sqlite").evaluate("r", "hash-y")

    mod = _load_script_module()
    monkeypatch.setattr(mod, "REPO_ROOT", clean_repo_root)
    monkeypatch.setattr(mod, "DEFAULT_DATA_DIR", data_dir)
    with pytest.raises(RunRefusedError, match="dirty"):
        mod.main(
            [
                "--config-hash",
                "hash-y",
                "--reason",
                "irrelevant",
                "--data-dir",
                str(data_dir),
            ]
        )

    # Lock should still be in place.
    assert HoldoutGuard(data_dir / "meta.sqlite").has_touched("hash-y") is True


def test_reset_unknown_hash_returns_nonzero(
    clean_repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = clean_repo_root / "data"
    data_dir.mkdir()
    # Initialize empty meta db without seeding any touches.
    HoldoutGuard(data_dir / "meta.sqlite")

    mod = _load_script_module()
    monkeypatch.setattr(mod, "REPO_ROOT", clean_repo_root)
    monkeypatch.setattr(mod, "DEFAULT_DATA_DIR", data_dir)
    exit_code = mod.main(
        [
            "--config-hash",
            "never-touched",
            "--reason",
            "doesn't matter",
            "--data-dir",
            str(data_dir),
        ]
    )
    assert exit_code == 1


def test_reset_missing_meta_db_returns_nonzero(
    clean_repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = clean_repo_root / "no_data"
    # Don't create data_dir at all.

    mod = _load_script_module()
    monkeypatch.setattr(mod, "REPO_ROOT", clean_repo_root)
    monkeypatch.setattr(mod, "DEFAULT_DATA_DIR", data_dir)
    exit_code = mod.main(
        [
            "--config-hash",
            "any",
            "--reason",
            "x",
            "--data-dir",
            str(data_dir),
        ]
    )
    assert exit_code == 1


def test_hook_script_exists_and_is_executable() -> None:
    hook = REPO_ROOT / "scripts" / "check_holdout_log_untouched.sh"
    assert hook.exists()
    content = hook.read_text(encoding="utf-8")
    assert "exit 1" in content
    assert "append-only" in content
    assert "reset_holdout_lock.py" in content


def test_hook_script_exits_nonzero_with_message() -> None:
    """Run the hook directly via Git-bash on Windows or system bash on POSIX.

    Pre-commit runs `language: system` hooks via the same bash that `git`
    ships with, so we look up that bash explicitly instead of relying on a
    bare `bash` on PATH (which is hijacked by WSL on Windows).
    """
    hook = REPO_ROOT / "scripts" / "check_holdout_log_untouched.sh"

    bash_exe: str | None = None
    if sys.platform.startswith("win"):
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                bash_exe = candidate
                break
    else:
        bash_exe = "bash"

    if bash_exe is None:
        pytest.skip("git-bash not found on this system")

    proc = subprocess.run(
        [bash_exe, str(hook), "data/runs/holdout_overrides.log"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "append-only" in proc.stderr
