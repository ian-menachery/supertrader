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

# 252 trading days/yr — matches `backtest.metrics.ANNUALIZATION_DAILY`.
# Used to translate the `max_turnover_annual` cap into a per-day budget.
_ANNUALIZATION: int = 252


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
        smoothing_alpha: float = 1.0,
        max_turnover_annual: float | None = None,
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
        if not 0 < smoothing_alpha <= 1.0:
            msg = f"smoothing_alpha must be in (0, 1], got {smoothing_alpha}"
            raise ValueError(msg)
        if max_turnover_annual is not None and max_turnover_annual <= 0:
            msg = f"max_turnover_annual must be positive when set, got {max_turnover_annual}"
            raise ValueError(msg)

        self._signal_name = signal_name
        self._quantile = quantile
        self._min_obs = min_signal_observations
        self._target_gross = target_gross
        self._direction: Direction = direction
        self._smoothing_alpha = smoothing_alpha
        self._max_turnover_annual = max_turnover_annual
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
        # Reindex prices to the signal's date index so per-date NaN lookups
        # line up. Any date in `aligned` not in `prices` becomes all-NaN
        # (effectively "no tradeable universe today") and produces a zero row.
        prices_aligned = prices.reindex(index=aligned.index, columns=aligned.columns)
        for date_idx, row in aligned.iterrows():
            non_null = row.dropna()
            # Out-of-universe leakage guard (added per platform-honesty pass):
            # restrict the ranking cross-section to tickers that ALSO have a
            # non-NaN price on this date. Without this, a ticker that's in the
            # signal panel but not actually tradeable today (e.g., delisted, or
            # outside a PIT universe on this date) would still contribute to
            # the rank distribution. See tests/unit/test_strategy_universe_guard.py.
            # mypy/pandas-stubs flags `.loc[Hashable]` as ambiguous over the
            # overload set; at runtime this is a row selection by index value.
            tradeable_today = prices_aligned.loc[date_idx].dropna().index  # type: ignore[call-overload]
            non_null = non_null[non_null.index.intersection(tradeable_today)]
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

        scaled = scale_to_gross(weights, target_gross=self._target_gross)
        return self._apply_position_persistence(scaled)

    def _apply_position_persistence(self, weights: pd.DataFrame) -> pd.DataFrame:
        """Apply EMA smoothing + per-day turnover cap, in that order.

        Both transforms are no-ops at default params (`smoothing_alpha=1.0`,
        `max_turnover_annual=None`). When set, they reduce day-to-day churn
        which (a) makes signals earn their cost before driving trades and
        (b) prevents silently-absurd turnover from leaving the strategy
        layer.

        EMA: `applied[t] = alpha * proposed[t] + (1 - alpha) * applied[t-1]`.

        Turnover cap: per-day turnover is `sum(|applied[t] - applied[t-1]|) / 2`.
        If the cap is set and binding, scale the per-day change pro-rata so
        the daily turnover equals the budget (`max_turnover_annual / 252`).
        """
        if self._smoothing_alpha >= 1.0 and self._max_turnover_annual is None:
            return weights
        alpha = self._smoothing_alpha
        daily_cap = (
            self._max_turnover_annual / _ANNUALIZATION
            if self._max_turnover_annual is not None
            else None
        )
        out = weights.copy()
        prev = pd.Series(0.0, index=weights.columns, dtype="float64")
        for date_idx in weights.index:
            proposed = weights.loc[date_idx].astype("float64")
            # Step 1: EMA smoothing.
            smoothed = alpha * proposed + (1.0 - alpha) * prev
            # Step 2: per-day turnover cap.
            if daily_cap is not None:
                change = smoothed - prev
                proposed_daily_turnover = float(change.abs().sum()) / 2.0
                if proposed_daily_turnover > daily_cap:
                    blend = daily_cap / proposed_daily_turnover
                    smoothed = prev + change * blend
            out.loc[date_idx] = smoothed
            prev = smoothed
        return out
