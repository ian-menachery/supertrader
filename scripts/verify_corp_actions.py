"""Verify yfinance corporate actions against a hand-curated golden file.

Pulls each ticker's split history from yfinance and diffs ex_date + ratio
against tests/golden/known_splits.csv. Prints a pass/fail table and exits
non-zero if pass rate falls below the threshold (default 80%).

Usage::

    uv run python scripts/verify_corp_actions.py
    uv run python scripts/verify_corp_actions.py --golden custom.csv --threshold 1.0
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = REPO_ROOT / "tests" / "golden" / "known_splits.csv"
RATIO_TOLERANCE = 0.01


def load_golden(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def fetch_yf_splits(ticker: str) -> pd.Series:
    splits = yf.Ticker(ticker).splits
    return pd.Series(splits) if splits is not None else pd.Series(dtype="float64")


def check_split(row: dict[str, str], yf_splits: pd.Series) -> tuple[bool, str]:
    expected_date = date.fromisoformat(row["ex_date"])
    expected_ratio = float(row["ratio"])
    if yf_splits.empty:
        return False, "yfinance returned no splits"

    # yfinance indexes by tz-aware Timestamp; normalize via DatetimeIndex to date
    idx_dates = pd.DatetimeIndex(yf_splits.index).date
    mask = idx_dates == expected_date
    matches = yf_splits[mask]
    if matches.empty:
        return False, f"no split on {expected_date}"
    actual_ratio = float(matches.iloc[0])
    if abs(actual_ratio - expected_ratio) > RATIO_TOLERANCE:
        return False, f"ratio mismatch expected={expected_ratio} actual={actual_ratio}"
    return True, f"ratio={actual_ratio}"


def verify_all(
    golden_path: Path,
    *,
    fetch_fn: Any = None,
) -> tuple[int, int, list[dict[str, Any]]]:
    # Resolve default at call time so monkeypatching the module attribute works.
    fn = fetch_fn if fetch_fn is not None else fetch_yf_splits
    rows = load_golden(golden_path)
    splits_by_ticker: dict[str, pd.Series] = {}
    results: list[dict[str, Any]] = []

    for row in rows:
        ticker = row["ticker"]
        if row["action_type"] != "split":
            results.append({**row, "status": "SKIP", "detail": "non-split rows not yet supported"})
            continue
        if ticker not in splits_by_ticker:
            splits_by_ticker[ticker] = fn(ticker)
        passed, detail = check_split(row, splits_by_ticker[ticker])
        results.append({**row, "status": "PASS" if passed else "FAIL", "detail": detail})

    passed_count = sum(1 for r in results if r["status"] == "PASS")
    total_checks = sum(1 for r in results if r["status"] != "SKIP")
    return passed_count, total_checks, results


def print_table(results: list[dict[str, Any]]) -> None:
    header = f"{'TICKER':6} {'EX_DATE':12} {'TYPE':6} {'EXPECTED':10} {'STATUS':6} DETAIL"
    print(header)
    print("-" * len(header))
    for r in results:
        expected = r.get("ratio", "")
        print(
            f"{r['ticker']:6} {r['ex_date']:12} {r['action_type']:6} "
            f"{expected:10} {r['status']:6} {r['detail']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Fraction of rows that must pass (default 0.8 = 80%%)",
    )
    args = parser.parse_args(argv)

    passed, total, results = verify_all(args.golden)
    print_table(results)
    if total == 0:
        print("\nNo checks to evaluate.")
        return 1
    pass_rate = passed / total
    print(f"\nSummary: {passed}/{total} passed ({pass_rate * 100:.1f}%)")
    print(f"Threshold: {args.threshold * 100:.0f}%")
    if pass_rate >= args.threshold:
        print("HARD GATE: PASS")
        return 0
    print("HARD GATE: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
