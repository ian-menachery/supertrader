"""Cross-sectional long/short strategy keyed off a single named signal.

For each date, rank universe-valid tickers by the configured signal, then:

  * `direction="mean_reversion"` (default): long the *bottom* `quantile`
    (most negative signal → expected to mean-revert up), short the top.
  * `direction="momentum"`: long the *top* `quantile` (most positive
    signal → expected to keep going), short the bottom.

Equal weight within each bucket. Final per-row weights are rescaled so total
gross exposure equals `target_gross` (default 1.0) via `strategies.risk`.

Days with fewer than `min_signal_observations` non-null signal values produce
zero-weight rows — no trades when the cross-section is too thin to rank.

The momentum branch exists for a specific diagnostic: ADR 0005's discipline
holds either way, but if mean-reversion produces train-Sharpe < 0 and
momentum produces train-Sharpe > 0 on the same data, that tells us the
strategy direction was inverted — separate from "does the signal have
information." See `docs/verdicts/rsm-v1-backtest.md` for context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from supertrader.config.registry import strategies
from supertrader.strategies.base import Strategy
from supertrader.strategies.risk import scale_to_gross

if TYPE_CHECKING:
    pass


Direction = Literal["mean_reversion", "momentum"]
_VALID_DIRECTIONS: tuple[Direction, ...] = ("mean_reversion", "momentum")


@strategies.register("mean_reversion")
class MeanReversionStrategy(Strategy):
    """Cross-sectional long/short ranking by signal value.

    Despite the class name (kept for back-compat with the existing config
    registry), the `direction` param flips the sign so the same code path
    serves both mean-reversion (default) and momentum variants.
    """

    strategy_id: str = "mean_reversion"

    def __init__(
        self,
        *,
        signal_name: str,
        quantile: float = 0.3,
        min_signal_observations: int = 5,
        target_gross: float = 1.0,
        direction: Direction = "mean_reversion",
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
        if direction not in _VALID_DIRECTIONS:
            msg = f"direction must be one of {_VALID_DIRECTIONS}, got {direction!r}"
            raise ValueError(msg)

        self._signal_name = signal_name
        self._quantile = quantile
        self._min_obs = min_signal_observations
        self._target_gross = target_gross
        self._direction: Direction = direction
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
            bottom = sorted_tickers.head(cutoff).index
            top = sorted_tickers.tail(cutoff).index
            if self._direction == "mean_reversion":
                longs, shorts = bottom, top
            else:  # momentum
                longs, shorts = top, bottom
            weights.loc[date_idx, longs] = 1.0 / cutoff
            weights.loc[date_idx, shorts] = -1.0 / cutoff

        return scale_to_gross(weights, target_gross=self._target_gross)
