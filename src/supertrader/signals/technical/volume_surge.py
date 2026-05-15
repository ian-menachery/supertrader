"""VolumeSurgeSignal — abnormal-volume + positive-return co-occurrence.

Event-style signal: scores are NaN on most (date, ticker) cells and
emit a value only when a same-day abnormal volume + positive return
occurs. Per ADR 0009, sparse panels are the chosen shape for event-
driven signals — the strategy ranker handles NaN-dominated rows
naturally (uses non-null entries for the cross-sectional cutoff).

For each (date T, ticker):

    abnormal_vol(T) = volume[T] / mean(volume[T-19..T-1])
    ret(T)          = close[T] / close[T-1] - 1
    if abnormal_vol > 2.0 AND ret > 0:
        score(T) = log(abnormal_vol) * ret
    else:
        score(T) = NaN

Reference: retail-trading literature on "volume confirmation" of price
moves; not as canonical as momentum / reversal but well-documented in
the practitioner space. The intent is forward-momentum: the abnormal
day signals attention; the positive return signals direction.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import polars as pl

from supertrader.config.registry import signals
from supertrader.signals.base import PointInTimeStore, Signal

PRICES_SOURCE_ID: str = "yfinance.prices.daily"


@signals.register("volume_surge")
class VolumeSurgeSignal(Signal):
    """Abnormal volume + positive return → forward-momentum entry score."""

    signal_id: str = "volume_surge"

    def __init__(
        self,
        *,
        lookback_days: int = 20,
        abnormal_vol_threshold: float = 2.0,
    ) -> None:
        if lookback_days < 5:
            msg = f"lookback_days must be at least 5, got {lookback_days}"
            raise ValueError(msg)
        if abnormal_vol_threshold <= 1.0:
            msg = f"abnormal_vol_threshold must be > 1.0, got {abnormal_vol_threshold}"
            raise ValueError(msg)
        self._lookback_days = lookback_days
        self._threshold = abnormal_vol_threshold
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
            .select(["date", "ticker", "close", "volume"])
            .collect()
        )
        if prices.is_empty():
            return _empty_panel(start, end, universe)

        pdf = prices.to_pandas()
        wide_close = pdf.pivot(index="date", columns="ticker", values="close")
        wide_vol = pdf.pivot(index="date", columns="ticker", values="volume").astype("float64")
        wide_close.index = pd.to_datetime(wide_close.index, utc=True)
        wide_close.index.name = "date"
        wide_vol.index = pd.to_datetime(wide_vol.index, utc=True)
        wide_vol.index.name = "date"

        returns = wide_close.pct_change()
        # Trailing-N-day average of volume, EXCLUDING today.
        prev_vol = wide_vol.shift(1)
        rolling_vol = prev_vol.rolling(
            window=self._lookback_days, min_periods=self._lookback_days
        ).mean()
        # Replace zero-mean volume rows with NaN (avoids division-by-zero on
        # newly-listed names with no trading-day history).
        rolling_vol = rolling_vol.where(rolling_vol > 0)
        abnormal = wide_vol / rolling_vol

        condition = (abnormal > self._threshold) & (returns > 0)
        # pandas/numpy interop: np.log on a DataFrame returns DataFrame at
        # runtime, but mypy thinks ndarray — wrap to pin the type.
        log_abnormal = pd.DataFrame(
            np.log(abnormal), index=abnormal.index, columns=abnormal.columns
        )
        raw_score = log_abnormal * returns
        # NaN where the condition fails; otherwise the score.
        score: pd.DataFrame = raw_score.where(condition)

        start_ts = pd.Timestamp(datetime.combine(start, time.min, tzinfo=UTC))
        end_ts = pd.Timestamp(datetime.combine(end, time.min, tzinfo=UTC))
        score = score[(score.index >= start_ts) & (score.index <= end_ts)]
        return score.reindex(columns=universe).astype("float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (self._lookback_days, self._threshold)


def _empty_panel(start: date, end: date, universe: list[str]) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="B", tz="UTC", name="date")
    return pd.DataFrame(float("nan"), index=idx, columns=universe, dtype="float64")
