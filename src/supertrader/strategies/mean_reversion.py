"""Cross-sectional mean-reversion strategy.

For each date, rank universe-valid tickers by the configured signal:
  * long the bottom `quantile` (most negative sentiment → expected mean revert up)
  * short the top `quantile` (most positive sentiment → expected mean revert down)
  * zero-weight everyone in between

Equal weight within each bucket. Final per-row weights are rescaled so total
gross exposure equals `target_gross` (default 1.0) via `strategies.risk`.

Days with fewer than `min_signal_observations` non-null signal values produce
zero-weight rows — no trades when the cross-section is too thin to rank.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from supertrader.config.registry import strategies
from supertrader.strategies.base import Strategy
from supertrader.strategies.risk import scale_to_gross

if TYPE_CHECKING:
    pass


@strategies.register("mean_reversion")
class MeanReversionStrategy(Strategy):
    """Cross-sectional long/short ranking by signal value."""

    strategy_id: str = "mean_reversion"

    def __init__(
        self,
        *,
        signal_name: str,
        quantile: float = 0.3,
        min_signal_observations: int = 5,
        target_gross: float = 1.0,
    ) -> None:
        if not 0 < quantile <= 0.5:
            msg = f"quantile must be in (0, 0.5], got {quantile}"
            raise ValueError(msg)
        if min_signal_observations < 2:
            msg = f"min_signal_observations must be >= 2, got {min_signal_observations}"
            raise ValueError(msg)
        if target_gross <= 0:
            msg = f"target_gross must be positive, got {target_gross}"
            raise ValueError(msg)

        self._signal_name = signal_name
        self._quantile = quantile
        self._min_obs = min_signal_observations
        self._target_gross = target_gross
        self.required_signals: tuple[str, ...] = (signal_name,)

    def target_positions(
        self,
        signals: dict[str, pd.DataFrame],
        prices: pd.DataFrame,
    ) -> pd.DataFrame:
        if self._signal_name not in signals:
            msg = (
                f"Strategy expects signal '{self._signal_name}' but received "
                f"only {sorted(signals.keys())}"
            )
            raise KeyError(msg)
        signal_panel = signals[self._signal_name]

        # Align to the universe of price columns — strategy only trades names with prices.
        common_tickers = [c for c in signal_panel.columns if c in prices.columns]
        if not common_tickers:
            return pd.DataFrame(0.0, index=signal_panel.index, columns=prices.columns)
        aligned = signal_panel[common_tickers]

        weights = pd.DataFrame(0.0, index=aligned.index, columns=prices.columns, dtype="float64")
        for date_idx, row in aligned.iterrows():
            non_null = row.dropna()
            if len(non_null) < self._min_obs:
                continue
            ranks = non_null.rank(method="average")
            n = len(non_null)
            cutoff = int(np.floor(n * self._quantile))
            if cutoff == 0:
                continue
            sorted_tickers = ranks.sort_values()
            longs = sorted_tickers.head(cutoff).index
            shorts = sorted_tickers.tail(cutoff).index
            weights.loc[date_idx, longs] = 1.0 / cutoff
            weights.loc[date_idx, shorts] = -1.0 / cutoff

        return scale_to_gross(weights, target_gross=self._target_gross)
