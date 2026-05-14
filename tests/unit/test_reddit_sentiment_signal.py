"""Tests for `RedditSentimentSignal` — aggregation, determinism, fingerprint, edge cases."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
from numpy.typing import NDArray

from supertrader.config.registry import signals
from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.reddit_sentiment.scorer_base import SentimentScorer
from supertrader.signals.reddit_sentiment.signal import RedditSentimentSignal

UNIVERSE: set[str] = {"AAPL", "TSLA", "GME", "NVDA"}
DAY1 = pd.Timestamp(2024, 1, 2, tz="UTC")
DAY2 = pd.Timestamp(2024, 1, 3, tz="UTC")


def _val(panel: pd.DataFrame, ts: pd.Timestamp, col: str) -> float:
    """Narrow pandas' wide return-type union to float for assertions."""
    return float(panel.at[ts, col])  # type: ignore[arg-type]


class _FakeScorer(SentimentScorer):
    """Deterministic scorer keyed by exact text match.

    Returns score_map[text] for known texts, 0.0 otherwise.
    """

    scorer_id: str = "fake"
    model_version: str = "fake-v0"

    def __init__(self, score_map: dict[str, float]) -> None:
        self.score_map = score_map

    def score(self, texts: list[str]) -> NDArray[np.float64]:
        return np.array(
            [self.score_map.get(t.strip(), 0.0) for t in texts], dtype=np.float64
        )


def _seed_store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(tmp_path)
    frame = pl.LazyFrame(
        {
            "id": ["p1", "p2", "p3", "p4", "p5"],
            "subreddit": ["wsb"] * 5,
            "year_month": ["2024-01"] * 5,
            "author": ["a", "b", "c", "d", "e"],
            "created_utc": [
                datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 2, 22, 0, tzinfo=UTC),
                datetime(2024, 1, 3, 8, 0, tzinfo=UTC),
                datetime(2024, 1, 3, 15, 0, tzinfo=UTC),
                datetime(2024, 1, 4, 12, 0, tzinfo=UTC),  # mentions no universe ticker
            ],
            "title": [
                "AAPL bullish",
                "AAPL bearish",
                "TSLA bullish",
                "TSLA bearish",
                "random nonsense",
            ],
            "selftext": [""] * 5,
            "score": [100, 1, 50, 50, 10],
            "num_comments": [0] * 5,
            "url": [""] * 5,
            "permalink": [""] * 5,
        }
    )
    store.write("arctic_shift.posts", frame, partition_keys=("subreddit", "year_month"))
    return store


SCORE_MAP: dict[str, float] = {
    "AAPL bullish": 0.8,
    "AAPL bearish": -0.6,
    "TSLA bullish": 0.7,
    "TSLA bearish": -0.5,
    "random nonsense": 0.0,
}


class TestMeanAggregation:
    def test_mean_per_day_per_ticker(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        sig = RedditSentimentSignal(
            scorer=_FakeScorer(SCORE_MAP),
            universe=UNIVERSE,
            aggregation="mean",
        )
        out = sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))

        assert set(out.columns) == UNIVERSE
        # AAPL day 1: mean of 0.8 and -0.6 = 0.1
        assert _val(out, DAY1, "AAPL") == pytest.approx(0.1)
        # TSLA day 2: mean of 0.7 and -0.5 = 0.1
        assert _val(out, DAY2, "TSLA") == pytest.approx(0.1)
        # GME and NVDA never mentioned -> all NaN columns
        assert out["GME"].isna().all()
        assert out["NVDA"].isna().all()


class TestScoreWeightedMean:
    def test_high_score_post_dominates(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        sig = RedditSentimentSignal(
            scorer=_FakeScorer(SCORE_MAP),
            universe=UNIVERSE,
            aggregation="score_weighted_mean",
        )
        out = sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))

        # AAPL day 1: post1 score=100 sentiment=0.8; post2 score=1 sentiment=-0.6
        # Weighted = (100*0.8 + 1*-0.6) / 101 = 79.4 / 101 ~= 0.786
        assert _val(out, DAY1, "AAPL") == pytest.approx(79.4 / 101)

    def test_differs_from_mean(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        mean_sig = RedditSentimentSignal(
            scorer=_FakeScorer(SCORE_MAP), universe=UNIVERSE, aggregation="mean"
        )
        weighted_sig = RedditSentimentSignal(
            scorer=_FakeScorer(SCORE_MAP),
            universe=UNIVERSE,
            aggregation="score_weighted_mean",
        )
        mean_out = mean_sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))
        wgt_out = weighted_sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))
        # AAPL day 1 differs: 0.1 vs ~0.786
        assert _val(mean_out, DAY1, "AAPL") != _val(wgt_out, DAY1, "AAPL")


class TestCountWeighted:
    def test_count_weighted_boosts_active_days(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        sig = RedditSentimentSignal(
            scorer=_FakeScorer(SCORE_MAP),
            universe=UNIVERSE,
            aggregation="count_weighted",
        )
        out = sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))
        # AAPL day 1: mean 0.1, count 2 -> 0.1 * log1p(2) ~= 0.1 * 1.0986
        assert _val(out, DAY1, "AAPL") == pytest.approx(0.1 * np.log1p(2.0))


class TestTimeDecayed:
    def test_late_day_post_weighted_more(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        sig = RedditSentimentSignal(
            scorer=_FakeScorer(SCORE_MAP),
            universe=UNIVERSE,
            aggregation="time_decayed",
            decay_halflife_hours=12.0,
        )
        out = sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))
        # AAPL day 1: post1 at 10:00 (14h to EOD), post2 at 22:00 (2h to EOD).
        # post2 has much higher weight; its sentiment is -0.6.
        # Result should be closer to -0.6 than to 0.8.
        assert _val(out, DAY1, "AAPL") < 0.0  # late-day bearish dominates


class TestDeterminism:
    def test_same_inputs_same_outputs(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        sig = RedditSentimentSignal(
            scorer=_FakeScorer(SCORE_MAP), universe=UNIVERSE
        )
        a = sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))
        b = sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), list(UNIVERSE))
        # Both have same shape and aligned NaN positions
        assert a.shape == b.shape
        # All non-NaN values match
        assert ((a.fillna(0) == b.fillna(0)).all()).all()


class TestFingerprint:
    def test_same_config_same_fingerprint(self) -> None:
        a = RedditSentimentSignal(scorer=_FakeScorer({}), universe=UNIVERSE)
        b = RedditSentimentSignal(scorer=_FakeScorer({}), universe=UNIVERSE)
        assert a.fingerprint() == b.fingerprint()

    def test_aggregation_changes_fingerprint(self) -> None:
        a = RedditSentimentSignal(
            scorer=_FakeScorer({}), universe=UNIVERSE, aggregation="mean"
        )
        b = RedditSentimentSignal(
            scorer=_FakeScorer({}), universe=UNIVERSE, aggregation="score_weighted_mean"
        )
        assert a.fingerprint() != b.fingerprint()

    def test_scorer_version_changes_fingerprint(self) -> None:
        class _OtherFake(_FakeScorer):
            model_version = "fake-v1"

        a = RedditSentimentSignal(scorer=_FakeScorer({}), universe=UNIVERSE)
        b = RedditSentimentSignal(scorer=_OtherFake({}), universe=UNIVERSE)
        assert a.fingerprint() != b.fingerprint()


class TestEdgeCases:
    def test_no_posts_in_window_returns_empty_panel(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2030, 1, 1))
        sig = RedditSentimentSignal(scorer=_FakeScorer(SCORE_MAP), universe=UNIVERSE)
        out = sig.compute(view, date(2030, 1, 1), date(2030, 1, 5), list(UNIVERSE))
        assert out.empty or out.isna().all().all()

    def test_runtime_universe_intersects_construction_universe(
        self, tmp_path: Path
    ) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        sig = RedditSentimentSignal(scorer=_FakeScorer(SCORE_MAP), universe=UNIVERSE)
        # Pass a runtime universe that doesn't include AAPL — those posts skip.
        out = sig.compute(view, date(2024, 1, 2), date(2024, 1, 4), ["TSLA", "GME"])
        assert "AAPL" not in out.columns
        assert "TSLA" in out.columns

    def test_invalid_aggregation_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid aggregation"):
            RedditSentimentSignal(
                scorer=_FakeScorer({}), universe=UNIVERSE, aggregation="garbage"  # type: ignore[arg-type]
            )

    def test_empty_universe_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty universe"):
            RedditSentimentSignal(scorer=_FakeScorer({}), universe=set())

    def test_negative_halflife_raises(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            RedditSentimentSignal(
                scorer=_FakeScorer({}), universe=UNIVERSE, decay_halflife_hours=-1.0
            )


class TestRegistry:
    def test_signal_is_registered(self) -> None:
        assert "reddit_sentiment" in signals
        assert signals.resolve("reddit_sentiment") is RedditSentimentSignal
