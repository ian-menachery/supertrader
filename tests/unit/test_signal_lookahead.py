"""Regression guard against look-ahead bias via the prices source.

Per `docs/known-limitations.md` #7: `PITStoreView.scan` filters by
`timestamp <= as_of`. For the daily-prices source, `as_of = T` includes
T's close. If a sentiment signal ever started reading prices (directly or
indirectly via its `required_sources` declaration), it would pick up T's
close to score a trade that fills at T+1's open — a real look-ahead.

Today `RedditSentimentSignal` only reads `arctic_shift.posts`, so this is
a no-op pin. The point is to fail loudly the day someone adds prices to
the signal's source list without also rewriting the execution-delay logic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from numpy.typing import NDArray

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.reddit_sentiment.scorer_base import SentimentScorer
from supertrader.signals.reddit_sentiment.signal import RedditSentimentSignal

FORBIDDEN_PRICE_SOURCE: str = "yfinance.prices.daily"


class _RecordingPITStore:
    """Wraps a real PITStoreView and records every `scan(source_id)` call.

    Implements the `PointInTimeStore` protocol (`as_of` attribute +
    `scan(source_id)` method) by delegation.
    """

    def __init__(self, inner: PITStoreView) -> None:
        self._inner = inner
        self.as_of: date = inner.as_of
        self.scanned: list[str] = []

    def scan(self, source_id: str) -> pl.LazyFrame:
        self.scanned.append(source_id)
        return self._inner.scan(source_id)


class _FakeScorer(SentimentScorer):
    scorer_id: str = "fake"
    model_version: str = "fake-v0"

    def score(self, texts: list[str]) -> NDArray[np.float64]:
        return np.zeros(len(texts), dtype=np.float64)


@pytest.fixture
def seeded_store(tmp_path: Path) -> ParquetStore:
    """A store with one minimal posts partition so the signal has something to read."""
    store = ParquetStore(tmp_path)
    frame = pl.LazyFrame(
        {
            "id": ["p1"],
            "subreddit": ["wsb"],
            "year_month": ["2024-01"],
            "author": ["alice"],
            "created_utc": [datetime(2024, 1, 2, 14, 0, tzinfo=UTC)],
            "title": ["$AAPL going up today"],
            "selftext": [""],
            "score": [10],
            "num_comments": [0],
            "url": [""],
            "permalink": [""],
        }
    )
    store.write("arctic_shift.posts", frame, partition_keys=("subreddit", "year_month"))
    return store


def test_reddit_sentiment_required_sources_excludes_prices() -> None:
    """The static `required_sources` declaration must never include prices."""
    signal = RedditSentimentSignal(
        scorer=_FakeScorer(),
        universe={"AAPL"},
        sources=("arctic_shift.posts",),
    )
    assert FORBIDDEN_PRICE_SOURCE not in signal.required_sources


def test_reddit_sentiment_compute_never_scans_prices(seeded_store: ParquetStore) -> None:
    """Runtime check: signal.compute() never asks the store for the prices source."""
    pit = PITStoreView(seeded_store, as_of=date(2024, 1, 3))
    recorder = _RecordingPITStore(pit)
    signal = RedditSentimentSignal(
        scorer=_FakeScorer(),
        universe={"AAPL"},
        sources=("arctic_shift.posts",),
    )
    signal.compute(recorder, date(2024, 1, 1), date(2024, 1, 5), ["AAPL"])
    assert FORBIDDEN_PRICE_SOURCE not in recorder.scanned
    assert recorder.scanned, "expected at least one scan() call (sanity check)"
