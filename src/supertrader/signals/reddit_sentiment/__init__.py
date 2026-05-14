"""Reddit sentiment signal: ticker extraction + pluggable scorer + per-day aggregation."""

from supertrader.signals.reddit_sentiment.scorer_base import SentimentScorer

__all__ = ["SentimentScorer"]
