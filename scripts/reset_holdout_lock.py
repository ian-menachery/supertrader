"""Clear the HoldoutGuard lock for a config_hash, recording the override.

The HoldoutGuard refuses a second holdout evaluation for the same config_hash.
This script is the one sanctioned way to release that lock — every run appends
one JSON-Lines record to `data/runs/holdout_overrides.log`, and the
`no-holdout-log-edit` pre-commit hook prevents hand-edits to that file.

Refuses to run on a dirty git tree: an override should be reproducible, and
that's only meaningful if the working tree is committed.

Usage::

    uv run python scripts/reset_holdout_lock.py \
        --config-hash <16+ hex> \
        --reason "re-eval after sentiment fix"

To commit the appended log line afterwards::

    git add data/runs/holdout_overrides.log
    git commit --no-verify -m "record holdout override: re-eval after sentiment fix"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from supertrader.backtest.splits import HoldoutGuard, HoldoutOverrideLog
from supertrader.observability.run_manifest import RunRefusedError, git_state

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-hash",
        required=True,
        help="The 32-char blake2b config hash whose holdout lock should be released.",
    )
    parser.add_argument(
        "--reason",
        required=True,
        help="One-line justification for the override (recorded in the log).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="ParquetStore root (default: repo/data/)",
    )
    parser.add_argument(
        "--operator",
        default=None,
        help="Override the auto-detected operator name.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("reset_holdout_lock")

    git_sha, git_dirty = git_state(REPO_ROOT)
    if git_dirty:
        msg = (
            "git tree is dirty; refusing to record an override. "
            "An override is only reproducible if the working tree is committed. "
            "Commit or stash your changes and retry."
        )
        raise RunRefusedError(msg)

    meta_db = args.data_dir / "meta.sqlite"
    if not meta_db.exists():
        log.error("meta sqlite not found at %s; nothing to clear", meta_db)
        return 1

    guard = HoldoutGuard(meta_db)
    existing = guard.clear(args.config_hash)
    if existing is None:
        log.error(
            "no holdout touch recorded for config_hash=%s; nothing to clear",
            args.config_hash[:16],
        )
        return 1
    original_run_id, original_touched_at = existing

    log_path = args.data_dir / "runs" / "holdout_overrides.log"
    override_log = HoldoutOverrideLog(log_path)
    record = override_log.append(
        config_hash=args.config_hash,
        original_run_id=original_run_id,
        original_touched_at=original_touched_at,
        reason=args.reason,
        git_sha=git_sha,
        operator=args.operator,
    )

    log.info("cleared holdout lock for config_hash=%s", args.config_hash[:16])
    log.info("logged override at %s", log_path)
    log.info("override record: %s", record)
    log.info(
        "next holdout touch for this config_hash will succeed once. "
        "Commit the log line with `git commit --no-verify -m '...'`."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RunRefusedError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
