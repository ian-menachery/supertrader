"""End-to-end integration test for RedditSentimentSignal.

Seeds a temporary ParquetStore with synthetic Arctic Shift posts, then runs
`compute()` against the live `VaderScorer` (no fakes) and the real ticker
blocklist. Verifies shape, ticker presence, and that the smoke run config
loads.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

# Importing the scorer modules triggers their @scorers.register decorators —
# critical for the registry-presence tests below.
import supertrader.signals.reddit_sentiment.scorer_finbert
import supertrader.signals.reddit_sentiment.scorer_llm  # noqa: F401
from supertrader.config.loader import load_run_config
from supertrader.config.registry import scorers, signals
from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.reddit_sentiment.scorer_vader import VaderScorer
from supertrader.signals.reddit_sentiment.signal import RedditSentimentSignal
from supertrader.signals.reddit_sentiment.ticker_extract import load_blocklist

REPO_ROOT = Path(__file__).resolve().parents[2]
LEXICON = REPO_ROOT / "configs" / "sentiment_lexicon.yaml"
BLOCKLIST = REPO_ROOT / "configs" / "ticker_blocklist.yaml"
SMOKE_YAML = REPO_ROOT / "configs" / "runs" / "smoke.yaml"

UNIVERSE: set[str] = {"AAPL", "TSLA", "NVDA", "GME"}


def _seed_store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(tmp_path)
    frame = pl.LazyFrame(
        {
            "id": [f"p{i}" for i in range(6)],
            "subreddit": ["wsb"] * 6,
            "year_month": ["2024-01"] * 6,
            "author": ["a"] * 6,
            "created_utc": [
                datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 2, 14, 0, tzinfo=UTC),
                datetime(2024, 1, 3, 9, 0, tzinfo=UTC),
                datetime(2024, 1, 3, 17, 0, tzinfo=UTC),
                datetime(2024, 1, 4, 11, 0, tzinfo=UTC),
                datetime(2024, 1, 4, 13, 0, tzinfo=UTC),
            ],
            "title": [
                "$AAPL bullish earnings beat coming",
                "AAPL bagholder dumping",
                "$TSLA short squeeze to the moon",
                "TSLA puts loaded, bearish",
                "$NVDA diamond hands incoming",
                "NVDA rug pull alert",
            ],
            "selftext": [""] * 6,
            "score": [100, 5, 80, 20, 60, 10],
            "num_comments": [0] * 6,
            "url": [""] * 6,
            "permalink": [""] * 6,
        }
    )
    store.write("arctic_shift.posts", frame, partition_keys=("subreddit", "year_month"))
    return store


class TestEndToEnd:
    def test_signal_runs_against_vader(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        view = PITStoreView(store, as_of=date(2024, 1, 5))
        scorer = VaderScorer(LEXICON)
        sig = RedditSentimentSignal(
            scorer=scorer,
            universe=UNIVERSE,
            aggregation="score_weighted_mean",
            blocklist=load_blocklist(BLOCKLIST),
        )
        out = sig.compute(view, date(2024, 1, 2), date(2024, 1, 5), list(UNIVERSE))

        # Shape: 3 days, 4 tickers
        assert set(out.columns) == UNIVERSE
        assert out.shape[0] >= 3
        # AAPL, TSLA, NVDA mentioned each on one day -> non-null somewhere
        assert not out["AAPL"].isna().all()
        assert not out["TSLA"].isna().all()
        assert not out["NVDA"].isna().all()
        # GME never mentioned -> all NaN
        assert out["GME"].isna().all()


class TestSmokeConfigLoads:
    def test_smoke_yaml_with_reddit_sentiment_loads(self) -> None:
        cfg = load_run_config(SMOKE_YAML)
        # Signal name is reddit_sentiment_v1
        assert cfg.signals[0].name == "reddit_sentiment_v1"
        assert cfg.signals[0].type == "reddit_sentiment"
        # Strategy references it by name
        assert "reddit_sentiment_v1" in cfg.strategy.signals
        # Scorer subconfig nested inside params
        scorer_cfg = cfg.signals[0].params["scorer"]
        assert scorer_cfg["type"] == "vader"


class TestRegistryWiring:
    def test_reddit_sentiment_in_signals_registry(self) -> None:
        assert "reddit_sentiment" in signals
        assert signals.resolve("reddit_sentiment") is RedditSentimentSignal

    def test_vader_in_scorers_registry(self) -> None:
        assert "vader" in scorers
        assert scorers.resolve("vader") is VaderScorer

    @pytest.mark.parametrize("scorer_key", ["vader", "finbert", "llm"])
    def test_all_three_scorer_types_registered(self, scorer_key: str) -> None:
        # Module-level imports above triggered registration.
        assert scorer_key in scorers
