"""Run RedditSentimentSignal against the real on-disk WSB backfill.

Loads the universe + blocklist + lexicon, computes the signal over the
available date range, and reports basic sanity stats:

* Number of trading-day rows
* Distinct tickers with at least one non-null score
* Score distribution (mean, std, min, max)
* Top-10 most-mentioned tickers
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import numpy as np

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.data.universe import StaticUniverse
from supertrader.signals.reddit_sentiment.scorer_vader import VaderScorer
from supertrader.signals.reddit_sentiment.signal import RedditSentimentSignal
from supertrader.signals.reddit_sentiment.ticker_extract import (
    extract_tickers,
    load_blocklist,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("validate")

    data_dir = REPO_ROOT / "data"
    if not (data_dir / "store" / "arctic_shift" / "posts").exists():
        log.error("No Arctic Shift data found at %s. Run backfill_wsb.py first.", data_dir)
        return 1

    store = ParquetStore(data_dir)

    # Determine the available date range from the store
    posts = store.scan("arctic_shift.posts").select("created_utc").collect()
    min_ts = posts["created_utc"].min()
    max_ts = posts["created_utc"].max()
    log.info(
        "available posts: %d rows from %s to %s", posts.height, str(min_ts), str(max_ts)
    )

    universe_loader = StaticUniverse.from_csv(
        REPO_ROOT / "configs" / "universe" / "snapshot_2026_05_14.csv"
    )
    universe = set(universe_loader.tickers())
    blocklist = load_blocklist(REPO_ROOT / "configs" / "ticker_blocklist.yaml")

    log.info("universe: %d tickers; blocklist: %d entries", len(universe), len(blocklist))

    scorer = VaderScorer(REPO_ROOT / "configs" / "sentiment_lexicon.yaml")
    sig = RedditSentimentSignal(
        scorer=scorer,
        universe=universe,
        aggregation="score_weighted_mean",
        blocklist=blocklist,
    )

    start = min_ts.date() if min_ts else date(2024, 1, 1)  # type: ignore[union-attr]
    end = max_ts.date() if max_ts else date(2024, 4, 1)  # type: ignore[union-attr]
    view = PITStoreView(store, as_of=end)

    log.info("computing signal over %s..%s", start, end)
    panel = sig.compute(view, start, end, list(universe))

    print(f"\nPanel shape: {panel.shape}")
    print(f"Trading-day rows: {panel.shape[0]}")
    non_null_cols = panel.columns[~panel.isna().all().to_numpy()]
    print(f"Tickers with at least one non-null score: {len(non_null_cols)}")
    print(f"  -> {sorted(non_null_cols.tolist())[:20]}")

    flat = panel.to_numpy().flatten()
    flat = flat[~np.isnan(flat)]
    if len(flat) > 0:
        print(f"\nScore distribution (over {len(flat)} non-null cells):")
        print(f"  mean:   {flat.mean():.4f}")
        print(f"  stddev: {flat.std():.4f}")
        print(f"  min:    {flat.min():.4f}")
        print(f"  max:    {flat.max():.4f}")

    # Top-mentioned tickers — count non-null cells per ticker
    mention_counts = (~panel.isna()).sum().sort_values(ascending=False).head(10)
    print("\nTop 10 most-mentioned tickers (by non-null cells):")
    print(mention_counts.to_string())

    # Cross-check against extract_tickers on a tiny sample
    sample = store.scan("arctic_shift.posts").head(5).collect()
    print("\nSample ticker extraction on 5 random posts:")
    for row in sample.iter_rows(named=True):
        text = f"{row['title']} {row['selftext'] or ''}"
        tickers = extract_tickers(text, universe, blocklist=blocklist)
        print(f"  {row['id']}: {tickers}")

    # Hard-gate sanity
    if len(non_null_cols) >= 10:
        log.info("VALIDATION OK: %d tickers have signal data", len(non_null_cols))
        return 0
    log.error(
        "VALIDATION FAILED: only %d tickers have signal data (need >= 10)",
        len(non_null_cols),
    )
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
