#!/usr/bin/env bash
# Pre-commit guard for data/runs/holdout_overrides.log.
#
# This file is append-only and is managed by scripts/reset_holdout_lock.py.
# Direct edits or commits to it must be refused so the audit trail stays
# trustworthy. When the reset script has run and a new line needs to land,
# commit with `git commit --no-verify` and justify in the commit message.
set -euo pipefail

echo "ERROR: data/runs/holdout_overrides.log is append-only and managed by" >&2
echo "       scripts/reset_holdout_lock.py. Direct commits are blocked." >&2
echo "" >&2
echo "       If you ran reset_holdout_lock.py and need to commit the new line," >&2
echo "       run:  git commit --no-verify -m '<justification>'" >&2
exit 1
