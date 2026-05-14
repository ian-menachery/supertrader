"""Smoke test for ArcticShiftPostsSource against the live HTTP API.

Pulls a small window of wallstreetbets posts to validate the API and parquet
write end-to-end. Gated on RUN_NETWORK_TESTS=1 so it never runs in CI.

Usage::

    RUN_NETWORK_TESTS=1 uv run python scripts/smoke_arctic_shift.py
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from supertrader.data.sources.reddit_arctic_shift import ArcticShiftPostsSource
from supertrader.data.store import ParquetStore


def main() -> int:
    if os.environ.get("RUN_NETWORK_TESTS") != "1":
        print("Skipping live smoke: set RUN_NETWORK_TESTS=1 to run.")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("smoke_arctic_shift")

    # Small window: 1 week of wallstreetbets in early 2024 — definitely indexed.
    start = date(2024, 1, 15)
    end = date(2024, 1, 22)
    subreddit = "wallstreetbets"
    cap = 500  # safety: stop after 500 records even if more exist

    with tempfile.TemporaryDirectory() as tmp:
        store_root = Path(tmp) / "smoke_store"
        store = ParquetStore(store_root)
        with ArcticShiftPostsSource(max_records_per_subreddit_month=cap) as source:
            log.info("starting ingest %s..%s r/%s cap=%d", start, end, subreddit, cap)
            rows = source.ingest(start, end, [subreddit], store)
            log.info("wrote %d rows", rows)

        df = store.scan(source.source_id).collect()
        print(f"\nIngested {df.height} posts across {df['subreddit'].n_unique()} subreddit(s).")
        print(f"Date range: {df['created_utc'].min()!s} -> {df['created_utc'].max()!s}")
        sample = df.head(3).select(["id", "subreddit", "title", "score"]).to_pandas()
        print(f"Sample row:\n{sample.to_string()}")

        if df.height < 10:
            log.error("HARD GATE FAILED: expected >= 10 posts, got %d", df.height)
            return 1
        log.info(
            "HARD GATE OK: pulled %d posts from r/%s in [%s, %s]",
            df.height,
            subreddit,
            start,
            end,
        )

        # Schema sanity
        expected_cols = {
            "id",
            "subreddit",
            "year_month",
            "author",
            "created_utc",
            "title",
            "selftext",
            "score",
            "num_comments",
            "url",
            "permalink",
        }
        actual_cols = set(df.columns)
        missing = expected_cols - actual_cols
        if missing:
            log.error("Schema mismatch — missing columns: %s", missing)
            return 1

        print(f"\nNon-null counts: {dict(df.null_count().to_dicts()[0])}")
        # Cast non-null counts (which are actually null counts) into something readable
        for col in ["title", "score", "created_utc"]:
            null_pct = df.select(pl.col(col).is_null().mean()).item() * 100
            print(f"  {col}: {null_pct:.1f}% null")

    return 0


if __name__ == "__main__":
    sys.exit(main())
