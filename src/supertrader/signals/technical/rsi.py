"""RsiSignal — classic Wilder's Relative Strength Index, rescaled to [-1, 1].

Standard RSI is in [0, 100]; we rescale via `(rsi - 50) / 50` so that:
  *  0.0 ≡ RSI 50  (neutral)
  * -1.0 ≡ RSI 0   (extremely oversold)
  * +1.0 ≡ RSI 100 (extremely overbought)
  * -0.4 ≡ RSI 30  (canonical "oversold" threshold)
  * +0.4 ≡ RSI 70  (canonical "overbought" threshold)

Rescaling makes the signal symmetric around zero so the same threshold
shape works in `SignalThresholdStrategy` for long and short legs.

Enables rules like "buy when RSI < 30, exit at RSI 50" via:

    SignalThresholdStrategy(
        signal_name="rsi",
        long_entry=-0.4,            # RSI 30
        short_entry=0.4,            # RSI 70
        exit_threshold=0.0,         # RSI 50
    )

Uses Wilder's smoothing (RMA = recursive moving average with alpha = 1/N)
on gains and losses. NaN until at least `window+1` data points are
available (need one prior close to compute the first return).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import polars as pl

from supertrader.config.registry import signals
from supertrader.signals.base import PointInTimeStore, Signal

PRICES_SOURCE_ID: str = "yfinance.prices.daily"


@signals.register("rsi")
class RsiSignal(Signal):
    """Wilder's RSI rescaled to [-1, 1] for use with symmetric thresholds."""

    signal_id: str = "rsi"

    def __init__(self, *, window: int = 14) -> None:
        if window < 2:
            msg = f"window must be at least 2, got {window}"
            raise ValueError(msg)
        self._window = window
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

        history_start = start - timedelta(days=int(self._window * 4) + 10)

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

        # Wilder's RSI: separate gains and losses, smooth with alpha = 1/N.
        delta = wide.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1.0 / self._window, adjust=False, min_periods=self._window).mean()
        avg_loss = loss.ewm(alpha=1.0 / self._window, adjust=False, min_periods=self._window).mean()
        # When avg_loss == 0:
        #   * if avg_gain > 0 → series is monotonic up → RSI = 100 (max overbought).
        #   * if avg_gain == 0 → completely flat → RSI undefined (NaN).
        # Mirror logic for avg_gain == 0 → RSI = 0 if avg_loss > 0.
        rsi_0_100 = pd.DataFrame(50.0, index=avg_gain.index, columns=avg_gain.columns)
        nonzero_loss = avg_loss > 0
        rs = avg_gain.where(nonzero_loss) / avg_loss.where(nonzero_loss)
        rsi_0_100 = rsi_0_100.where(~nonzero_loss, 100.0 - (100.0 / (1.0 + rs)))
        # Where avg_loss == 0 and avg_gain > 0 → RSI = 100.
        zero_loss_with_gain = (avg_loss == 0) & (avg_gain > 0)
        rsi_0_100 = rsi_0_100.where(~zero_loss_with_gain, 100.0)
        # Where avg_gain == 0 and avg_loss > 0 → RSI = 0.
        zero_gain_with_loss = (avg_gain == 0) & (avg_loss > 0)
        rsi_0_100 = rsi_0_100.where(~zero_gain_with_loss, 0.0)
        # Where both zero (flat series) → RSI undefined → NaN.
        flat = (avg_gain == 0) & (avg_loss == 0)
        rsi_0_100 = rsi_0_100.where(~flat)
        # Rescale to [-1, 1]
        score = (rsi_0_100 - 50.0) / 50.0

        start_ts = pd.Timestamp(datetime.combine(start, time.min, tzinfo=UTC))
        end_ts = pd.Timestamp(datetime.combine(end, time.min, tzinfo=UTC))
        score = score[(score.index >= start_ts) & (score.index <= end_ts)]
        return score.reindex(columns=universe).astype("float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (self._window,)


def _empty_panel(start: date, end: date, universe: list[str]) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="B", tz="UTC", name="date")
    return pd.DataFrame(float("nan"), index=idx, columns=universe, dtype="float64")
