"""RedditSentimentSignal — per (date by ticker) sentiment panel from Reddit posts.

Pipeline:

    PIT scan -> filter to [start, end] -> for each post, extract universe tickers
    and score the combined title+selftext -> explode to per-(post, ticker) rows
    -> aggregate by (date, ticker) using the configured mode
    -> pivot wide and reindex to the full universe.

The output is a pandas DataFrame, DatetimeIndex in UTC, columns are tickers,
values are float64 (NaN where no posts mentioned the ticker on that date).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
import polars as pl

from supertrader.config.registry import signals
from supertrader.signals.base import PointInTimeStore, Signal
from supertrader.signals.reddit_sentiment.ticker_extract import extract_tickers

if TYPE_CHECKING:
    from supertrader.signals.reddit_sentiment.scorer_base import SentimentScorer


AggregationMode = Literal["mean", "score_weighted_mean", "count_weighted", "time_decayed"]
_VALID_MODES: tuple[str, ...] = (
    "mean",
    "score_weighted_mean",
    "count_weighted",
    "time_decayed",
)


@signals.register("reddit_sentiment")
class RedditSentimentSignal(Signal):
    """Aggregates Reddit-post sentiment into a (date by ticker) panel."""

    signal_id: str = "reddit_sentiment"

    def __init__(
        self,
        *,
        scorer: SentimentScorer,
        universe: set[str],
        aggregation: AggregationMode = "score_weighted_mean",
        decay_halflife_hours: float = 24.0,
        sources: tuple[str, ...] = ("arctic_shift.posts",),
        blocklist: set[str] | None = None,
    ) -> None:
        if aggregation not in _VALID_MODES:
            msg = f"Invalid aggregation '{aggregation}'. Valid: {_VALID_MODES}"
            raise ValueError(msg)
        if not universe:
            msg = "RedditSentimentSignal requires a non-empty universe"
            raise ValueError(msg)
        if decay_halflife_hours <= 0:
            msg = f"decay_halflife_hours must be positive, got {decay_halflife_hours}"
            raise ValueError(msg)

        self._scorer = scorer
        self._universe = frozenset(universe)
        self._aggregation: AggregationMode = aggregation
        self._decay_halflife_hours = decay_halflife_hours
        self._blocklist = frozenset(blocklist) if blocklist else frozenset()
        self.required_sources: tuple[str, ...] = sources

    def compute(
        self,
        store: PointInTimeStore,
        start: date,
        end: date,
        universe: list[str],
    ) -> pd.DataFrame:
        # Universe parameter may differ from the construction-time set (e.g.,
        # the strategy passes a runtime-filtered subset). Re-cast to set for lookups.
        runtime_universe = set(universe) & self._universe
        if not runtime_universe:
            return _empty_panel(start, end, universe)

        start_dt = datetime.combine(start, time.min, tzinfo=UTC)
        end_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)

        rows = self._collect_post_rows(store, start_dt, end_dt, runtime_universe)
        if rows is None or rows.is_empty():
            return _empty_panel(start, end, universe)

        long_df = self._explode_and_score(rows, runtime_universe)
        if long_df.empty:
            return _empty_panel(start, end, universe)

        agg = self._aggregate(long_df)
        wide = agg.unstack(level="ticker")
        wide = wide.reindex(columns=list(universe))
        wide.index = pd.to_datetime(wide.index, utc=True)
        wide.index.name = "date"
        return wide.astype("float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (
            self._scorer.model_version,
            self._aggregation,
            self._decay_halflife_hours,
            tuple(sorted(self.required_sources)),
            tuple(sorted(self._universe)),
            tuple(sorted(self._blocklist)),
        )

    # ─────────────────── internals ───────────────────

    def _collect_post_rows(
        self,
        store: PointInTimeStore,
        start_dt: datetime,
        end_dt: datetime,
        runtime_universe: set[str],
    ) -> pl.DataFrame | None:
        del runtime_universe  # used downstream; scan unfiltered for now
        frames: list[pl.DataFrame] = []
        for source_id in self.required_sources:
            try:
                lazy = store.scan(source_id)
            except FileNotFoundError:
                continue
            df = (
                lazy.filter(pl.col("created_utc") >= start_dt)
                .filter(pl.col("created_utc") < end_dt)
                .select(["id", "created_utc", "title", "selftext", "score"])
                .collect()
            )
            if not df.is_empty():
                frames.append(df)
        if not frames:
            return None
        return pl.concat(frames)

    def _explode_and_score(
        self, posts: pl.DataFrame, runtime_universe: set[str]
    ) -> pd.DataFrame:
        # Build combined text for scoring
        titles = posts["title"].fill_null("").to_list()
        bodies = posts["selftext"].fill_null("").to_list()
        texts = [f"{t} {b}".strip() for t, b in zip(titles, bodies, strict=True)]

        sentiments = self._scorer.score(texts)
        if len(sentiments) != len(texts):
            msg = (
                f"Scorer returned {len(sentiments)} scores for {len(texts)} texts — "
                "implementation contract violation."
            )
            raise RuntimeError(msg)

        timestamps = posts["created_utc"].to_list()
        post_scores = posts["score"].fill_null(0).to_list()
        blocklist_set = set(self._blocklist)

        def _gen() -> Iterator[dict[str, object]]:
            for text, ts, post_score, sentiment in zip(
                texts, timestamps, post_scores, sentiments, strict=True
            ):
                tickers = extract_tickers(text, runtime_universe, blocklist=blocklist_set)
                if not tickers:
                    continue
                bucket = ts.date()
                for ticker in tickers:
                    yield {
                        "date": bucket,
                        "ticker": ticker,
                        "sentiment": float(sentiment),
                        "post_score": max(int(post_score), 1),
                        "timestamp": ts,
                    }

        rows = list(_gen())
        return pd.DataFrame(
            rows,
            columns=["date", "ticker", "sentiment", "post_score", "timestamp"],
        )

    def _aggregate(self, long_df: pd.DataFrame) -> pd.Series:
        if self._aggregation == "mean":
            return long_df.groupby(["date", "ticker"])["sentiment"].mean()

        if self._aggregation == "score_weighted_mean":
            df = long_df.assign(
                _weighted=long_df["sentiment"] * long_df["post_score"].astype("float64")
            )
            grouped = df.groupby(["date", "ticker"])
            sum_weighted = grouped["_weighted"].sum()
            sum_weights = grouped["post_score"].sum().astype("float64")
            return sum_weighted / sum_weights.replace(0, np.nan)

        if self._aggregation == "count_weighted":
            grouped = long_df.groupby(["date", "ticker"])
            mean = grouped["sentiment"].mean()
            count = grouped.size().astype("float64")
            return mean * np.log1p(count)

        # time_decayed
        eod = pd.to_datetime(long_df["date"]).dt.tz_localize("UTC") + pd.Timedelta(days=1)
        hours_to_eod = (eod - long_df["timestamp"]).dt.total_seconds() / 3600.0
        decay_weight = np.exp(-np.log(2.0) * hours_to_eod / self._decay_halflife_hours)
        df = long_df.assign(
            _decay_weight=decay_weight,
            _decayed=long_df["sentiment"] * decay_weight,
        )
        grouped = df.groupby(["date", "ticker"])
        sum_decayed = grouped["_decayed"].sum()
        sum_weights = grouped["_decay_weight"].sum()
        return sum_decayed / sum_weights.replace(0, np.nan)


def _empty_panel(start: date, end: date, universe: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="date")
    del start, end  # signature consistency; index stays empty
    return pd.DataFrame(index=idx, columns=list(universe), dtype="float64")
