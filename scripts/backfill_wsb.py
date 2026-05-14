"""One-shot backfill of wallstreetbets posts via the Arctic Shift HTTP API.

Default window: 2024-01-01 to 2024-04-01 (~25K posts at ~250/day density).
Writes to the real `data/` directory in the repo root.

Usage::

    uv run python scripts/backfill_wsb.py
    uv run python scripts/backfill_wsb.py --start 2024-01-01 --end 2024-02-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from supertrader.data.sources.reddit_arctic_shift import ArcticShiftPostsSource
from supertrader.data.store import ParquetStore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 4, 1))
    parser.add_argument("--subreddit", default="wallstreetbets")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="ParquetStore root (default: repo/data/)",
    )
    parser.add_argument(
        "--max-per-month",
        type=int,
        default=None,
        help="Safety cap on records per subreddit-month (default: no cap)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("backfill_wsb")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    store = ParquetStore(args.data_dir)

    with ArcticShiftPostsSource(max_records_per_subreddit_month=args.max_per_month) as source:
        log.info(
            "starting backfill subreddit=%s window=%s..%s data_dir=%s",
            args.subreddit,
            args.start,
            args.end,
            args.data_dir,
        )
        rows = source.ingest(args.start, args.end, [args.subreddit], store)
        log.info("backfill complete: wrote %d rows", rows)

    df = store.scan(source.source_id).collect()
    print(f"\nTotal posts on disk: {df.height}")
    print(f"Subreddits: {df['subreddit'].unique().to_list()}")
    print(f"Months: {sorted(df['year_month'].unique().to_list())}")
    print(f"Date range: {df['created_utc'].min()!s} -> {df['created_utc'].max()!s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
