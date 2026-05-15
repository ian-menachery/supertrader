"""MovingAverageCrossSignal — fast vs slow moving average.

For each (date T, ticker):

    fast = mean(close[T-fast+1..T])
    slow = mean(close[T-slow+1..T])
    score(T) = (fast - slow) / slow

Positive → fast MA above slow MA → uptrend (golden cross territory).
Negative → fast MA below slow MA → downtrend (death cross).

The normalization by `slow` makes scores comparable across tickers
with different price levels — a $400 stock and a $40 stock both
produce ~0.05 when the fast MA sits 5% above the slow MA.

Enables rules like "buy when 20d MA crosses above 50d MA" via:

    SignalThresholdStrategy(
        signal_name="ma_cross",
        long_entry=0.0,
        short_entry=None,        # long-only crossover follower
        exit_threshold=-0.005,
    )
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import polars as pl

from supertrader.config.registry import signals
from supertrader.signals.base import PointInTimeStore, Signal

PRICES_SOURCE_ID: str = "yfinance.prices.daily"


@signals.register("ma_cross")
class MovingAverageCrossSignal(Signal):
    """Normalized difference between a fast and slow rolling mean of close."""

    signal_id: str = "ma_cross"

    def __init__(self, *, fast_window: int = 20, slow_window: int = 50) -> None:
        if fast_window < 2:
            msg = f"fast_window must be at least 2, got {fast_window}"
            raise ValueError(msg)
        if slow_window <= fast_window:
            msg = (
                f"slow_window ({slow_window}) must be strictly greater than "
                f"fast_window ({fast_window})"
            )
            raise ValueError(msg)
        self._fast = fast_window
        self._slow = slow_window
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

        history_start = start - timedelta(days=int(self._slow * 2) + 10)

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

        fast_ma = wide.rolling(window=self._fast, min_periods=self._fast).mean()
        slow_ma = wide.rolling(window=self._slow, min_periods=self._slow).mean()
        # Avoid divide-by-zero on flat series.
        safe_slow = slow_ma.where(slow_ma > 0)
        score = (fast_ma - slow_ma) / safe_slow

        start_ts = pd.Timestamp(datetime.combine(start, time.min, tzinfo=UTC))
        end_ts = pd.Timestamp(datetime.combine(end, time.min, tzinfo=UTC))
        score = score[(score.index >= start_ts) & (score.index <= end_ts)]
        return score.reindex(columns=universe).astype("float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (self._fast, self._slow)


def _empty_panel(start: date, end: date, universe: list[str]) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="B", tz="UTC", name="date")
    return pd.DataFrame(float("nan"), index=idx, columns=universe, dtype="float64")
