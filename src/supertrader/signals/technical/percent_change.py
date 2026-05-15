"""PercentChangeSignal — N-day rolling percent change on close.

For each (date T, ticker):

    score(T) = close[T] / close[T - lookback_days] - 1

Enables rule-based strategies like "long when stock drops 2% in a
day" via:

    SignalThresholdStrategy(
        signal_name="drop_1d",
        long_entry=-0.02,
        short_entry=None,
        exit_threshold=0.0,
    )

The signal is signed and unbounded. NaN where insufficient history.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import polars as pl

from supertrader.config.registry import signals
from supertrader.signals.base import PointInTimeStore, Signal

PRICES_SOURCE_ID: str = "yfinance.prices.daily"


@signals.register("percent_change")
class PercentChangeSignal(Signal):
    """N-day percent change on close prices."""

    signal_id: str = "percent_change"

    def __init__(self, *, lookback_days: int = 1) -> None:
        if lookback_days < 1:
            msg = f"lookback_days must be at least 1, got {lookback_days}"
            raise ValueError(msg)
        self._lookback_days = lookback_days
        self.required_sources: tuple[str, ...] = (PRICES_SOURCE_ID,)

    def compute(
        self,
        store: PointInTimeStore,
        start: date,
        end: date,
        universe: list[str],
    ) -> pd.DataFrame:
        if not universe:
            return _empty_panel(start, end, universe)

        history_start = start - timedelta(days=int(self._lookback_days * 2) + 5)

        prices = (
            store.scan(PRICES_SOURCE_ID)
            .filter(pl.col("date") >= history_start)
            .filter(pl.col("date") <= end)
            .filter(pl.col("ticker").is_in(universe))
            .select(["date", "ticker", "close"])
            .collect()
        )
        if prices.is_empty():
            return _empty_panel(start, end, universe)

        wide = prices.to_pandas().pivot(index="date", columns="ticker", values="close")
        wide.index = pd.to_datetime(wide.index, utc=True)
        wide.index.name = "date"

        score = wide.pct_change(periods=self._lookback_days)

        start_ts = pd.Timestamp(datetime.combine(start, time.min, tzinfo=UTC))
        end_ts = pd.Timestamp(datetime.combine(end, time.min, tzinfo=UTC))
        score = score[(score.index >= start_ts) & (score.index <= end_ts)]
        return score.reindex(columns=universe).astype("float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (self._lookback_days,)


def _empty_panel(start: date, end: date, universe: list[str]) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="B", tz="UTC", name="date")
    return pd.DataFrame(float("nan"), index=idx, columns=universe, dtype="float64")
