"""ZScoreReversalSignal — short-term cross-sectional mean reversion on returns.

For each (date T, ticker), the score is the z-score of today's daily
return against the previous 20 days' returns:

    ret[T]      = close[T] / close[T-1] - 1
    mean_20(T)  = mean(ret[T-19..T-1])
    std_20(T)   = std(ret[T-19..T-1])
    score(T)    = (ret[T] - mean_20(T)) / std_20(T)

References: Lehmann (1990), "Fads, martingales, and market efficiency";
Lo & MacKinlay (1990), "When are contrarian profits due to stock-market
overreaction?". The cross-sectional ranker downstream goes long the
most-negative-z and short the most-positive-z (mean-reversion direction).

NaN when std_20 == 0 (flat returns over the lookback) or when there's
insufficient history.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import polars as pl

from supertrader.config.registry import signals
from supertrader.signals.base import PointInTimeStore, Signal

PRICES_SOURCE_ID: str = "yfinance.prices.daily"


@signals.register("zscore_reversal")
class ZScoreReversalSignal(Signal):
    """Rolling z-score of daily returns; pairs with direction='mean_reversion'."""

    signal_id: str = "zscore_reversal"

    def __init__(self, *, lookback_days: int = 20) -> None:
        if lookback_days < 5:
            msg = f"lookback_days must be at least 5, got {lookback_days}"
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

        wide_close = prices.to_pandas().pivot(index="date", columns="ticker", values="close")
        wide_close.index = pd.to_datetime(wide_close.index, utc=True)
        wide_close.index.name = "date"

        returns = wide_close.pct_change()
        # Rolling stats over the previous N days, EXCLUDING today, so the
        # z-score is "today vs the recent past." Shift by 1 to drop today
        # from the window, then look back over `lookback_days` rows.
        prev_returns = returns.shift(1)
        rolling_mean = prev_returns.rolling(
            window=self._lookback_days, min_periods=self._lookback_days
        ).mean()
        rolling_std = prev_returns.rolling(
            window=self._lookback_days, min_periods=self._lookback_days
        ).std()
        # Replace zero-std cells with NaN so the division yields NaN, not Inf
        rolling_std = rolling_std.where(rolling_std > 0)
        score = (returns - rolling_mean) / rolling_std

        start_ts = pd.Timestamp(datetime.combine(start, time.min, tzinfo=UTC))
        end_ts = pd.Timestamp(datetime.combine(end, time.min, tzinfo=UTC))
        score = score[(score.index >= start_ts) & (score.index <= end_ts)]
        return score.reindex(columns=universe).astype("float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (self._lookback_days,)


def _empty_panel(start: date, end: date, universe: list[str]) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="B", tz="UTC", name="date")
    return pd.DataFrame(float("nan"), index=idx, columns=universe, dtype="float64")
