"""CrossSectionalMomentumSignal — classic Jegadeesh & Titman 12-1 momentum.

For each (date T, ticker), the score is the trailing return from T-252 to
T-21:

    score(T) = close[T-21] / close[T-252] - 1

The 21-day "skip month" excludes the short-term reversal effect that
dominates the most recent month, isolating the longer-horizon momentum
component. Reference: Jegadeesh & Titman (1993).

The signal is a continuous cross-sectional score; the strategy ranks
universe tickers by it and goes long the top decile / short the bottom
(`MeanReversionStrategy(direction="momentum")`).

NaN values appear where insufficient history is available (< 252 trading
days back). The strategy's `min_signal_observations` filter handles thin
days naturally.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

import pandas as pd
import polars as pl

from supertrader.config.registry import signals
from supertrader.signals.base import PointInTimeStore, Signal

if TYPE_CHECKING:
    pass


PRICES_SOURCE_ID: str = "yfinance.prices.daily"


@signals.register("cross_sectional_momentum")
class CrossSectionalMomentumSignal(Signal):
    """12-month-minus-1-month cross-sectional momentum on daily closes."""

    signal_id: str = "cross_sectional_momentum"

    def __init__(
        self,
        *,
        lookback_days: int = 252,
        skip_days: int = 21,
    ) -> None:
        if lookback_days <= skip_days:
            msg = f"lookback_days ({lookback_days}) must exceed skip_days ({skip_days})"
            raise ValueError(msg)
        if skip_days < 0:
            msg = f"skip_days must be non-negative, got {skip_days}"
            raise ValueError(msg)
        self._lookback_days = lookback_days
        self._skip_days = skip_days
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

        # We need history reaching back lookback_days before `start` to be able
        # to score the very first day in the output window.
        history_start = start - timedelta(days=int(self._lookback_days * 1.5) + 30)

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

        # Apply the 12-1 formulation: today's score depends on close[t-skip]
        # divided by close[t-lookback]. Shift `skip` days forward to align the
        # "12-month-ago" baseline with the "1-month-ago" current price.
        recent = wide.shift(self._skip_days)
        past = wide.shift(self._lookback_days)
        score = recent / past - 1.0

        # Restrict output to the [start, end] window.
        start_ts = pd.Timestamp(datetime.combine(start, time.min, tzinfo=UTC))
        end_ts = pd.Timestamp(datetime.combine(end, time.min, tzinfo=UTC))
        score = score[(score.index >= start_ts) & (score.index <= end_ts)]
        return score.reindex(columns=universe).astype("float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (self._lookback_days, self._skip_days)


def _empty_panel(start: date, end: date, universe: list[str]) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="B", tz="UTC", name="date")
    return pd.DataFrame(float("nan"), index=idx, columns=universe, dtype="float64")
