"""Decompose the canonical test-window daily returns into Q3 and Q4 of 2023.

The full canonical test window 2023-07..2023-12 reported Sharpe = +1.34.
The diagnostic question (per docs/verdicts/rsm-v1-backtest.md) is whether
that's distributed across Q3 and Q4 evenly or concentrated in one regime.

This script re-executes the canonical pipeline in-process (same config,
same data, identical config_hash db0e8a836d7fbb8d72cd34d6309414fd —
counts as the same peek, NOT a new one) and slices the test_result
returns series into the two quarters. Sharpe is recomputed on each slice
using the same `sharpe()` helper the engine uses.

Usage::

    uv run python scripts/decompose_test_quarters.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

from supertrader.backtest.metrics import sharpe, sortino
from supertrader.pipelines.run_backtest import run_backtest

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_CONFIG = REPO_ROOT / "configs" / "runs" / "rsm_v1_backtest.yaml"

Q3_START = pd.Timestamp("2023-07-01", tz="UTC")
Q3_END = pd.Timestamp("2023-09-30", tz="UTC")
Q4_START = pd.Timestamp("2023-10-01", tz="UTC")
Q4_END = pd.Timestamp("2023-12-31", tz="UTC")


def _slice_window(returns: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    return returns[(returns.index >= start) & (returns.index <= end)]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("decompose_test_quarters")

    log.info("re-executing canonical config (same config_hash — single peek)")
    out = run_backtest(CANONICAL_CONFIG, include_holdout=False, allow_dirty=True)
    test_returns = out.test_result.returns
    if test_returns.empty:
        log.error("test_result.returns is empty; nothing to decompose")
        return 1

    log.info(
        "canonical test window: %s..%s, n=%d days, full-window Sharpe=%.4f",
        test_returns.index.min(),
        test_returns.index.max(),
        len(test_returns),
        sharpe(test_returns),
    )

    q3 = _slice_window(test_returns, Q3_START, Q3_END)
    q4 = _slice_window(test_returns, Q4_START, Q4_END)

    print("")
    header = (
        f"{'window':<10} {'n_days':>7} {'Sharpe':>10} "
        f"{'Sortino':>10} {'mean_ret':>12} {'cum_ret':>10}"
    )
    print(header)
    print("-" * 65)
    for label, series in [
        ("FULL test", test_returns),
        ("2023-Q3  ", q3),
        ("2023-Q4  ", q4),
    ]:
        if series.empty:
            print(f"{label}  (no data)")
            continue
        cum = float((1.0 + series).prod() - 1.0)
        print(
            f"{label:<10} {len(series):>7d} "
            f"{sharpe(series):>10.4f} {sortino(series):>10.4f} "
            f"{float(series.mean()):>12.6f} {cum:>10.4f}"
        )
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
