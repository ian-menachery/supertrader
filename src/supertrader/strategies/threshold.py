"""SignalThresholdStrategy — per-ticker time-series strategy.

Cross-sectional rankers (`MeanReversionStrategy`) compare tickers against
each other every day. SignalThresholdStrategy is different: each ticker
decides *independently* based on its own signal value crossing fixed
thresholds. Position count is variable day-to-day (could be 0, could be
all of universe), then scaled to `target_gross` at the end.

Built for rule-based ideas like:

    "long when the signal is below -2%, exit when it returns to 0"

    SignalThresholdStrategy(
        signal_name="drop",
        long_entry=-0.02,
        short_entry=None,        # long-only
        exit_threshold=0.0,
    )

State machine per ticker (long-only example, `short_entry=None`):

  flat ─── signal > long_entry ──> long
  long ─── signal < exit_threshold ──> flat

For long+short:

  flat ─── signal > long_entry ──> long
  flat ─── signal < short_entry ──> short
  long ─── signal < exit_threshold ──> flat
  short ── signal > -exit_threshold ──> flat

Position persistence (smoothing_alpha, max_turnover_annual) reuses the
shared `apply_position_persistence` helper. NaN-price tickers on date T
are excluded from that day's trading (same out-of-universe leakage guard
as MeanReversionStrategy).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pandas as pd

from supertrader.config.registry import strategies
from supertrader.strategies.base import Strategy
from supertrader.strategies.risk import apply_position_persistence, scale_to_gross

if TYPE_CHECKING:
    from collections.abc import Hashable


@strategies.register("signal_threshold")
class SignalThresholdStrategy(Strategy):
    """Per-ticker time-series strategy driven by signal thresholds."""

    strategy_id: str = "signal_threshold"

    def __init__(
        self,
        *,
        signal_name: str,
        long_entry: float,
        short_entry: float | None = None,
        exit_threshold: float = 0.0,
        position_size: float = 1.0,
        target_gross: float = 1.0,
        max_positions: int | None = None,
        smoothing_alpha: float = 1.0,
        max_turnover_annual: float | None = None,
    ) -> None:
        if short_entry is not None and short_entry >= long_entry:
            msg = (
                f"short_entry ({short_entry}) must be strictly less than "
                f"long_entry ({long_entry}); thresholds overlap"
            )
            raise ValueError(msg)
        if position_size <= 0:
            msg = f"position_size must be positive, got {position_size}"
            raise ValueError(msg)
        if target_gross <= 0:
            msg = f"target_gross must be positive, got {target_gross}"
            raise ValueError(msg)
        if max_positions is not None and max_positions < 1:
            msg = f"max_positions must be at least 1 when set, got {max_positions}"
            raise ValueError(msg)
        if not 0 < smoothing_alpha <= 1.0:
            msg = f"smoothing_alpha must be in (0, 1], got {smoothing_alpha}"
            raise ValueError(msg)
        if max_turnover_annual is not None and max_turnover_annual <= 0:
            msg = f"max_turnover_annual must be positive when set, got {max_turnover_annual}"
            raise ValueError(msg)

        self._signal_name = signal_name
        self._long_entry = long_entry
        self._short_entry = short_entry
        self._exit_threshold = exit_threshold
        self._position_size = position_size
        self._target_gross = target_gross
        self._max_positions = max_positions
        self._smoothing_alpha = smoothing_alpha
        self._max_turnover_annual = max_turnover_annual
        self.required_signals: tuple[str, ...] = (signal_name,)

    def _next_position(self, current: int, signal_float: float) -> int:
        """Apply the per-ticker state machine to one (current, signal) pair.

        Exits run before entries so today's signal cannot flip a position
        directly from long to short (or vice versa) without crossing flat.
        """
        long_exit = current == 1 and signal_float < self._exit_threshold
        short_exit = current == -1 and signal_float > -self._exit_threshold
        if long_exit or short_exit:
            current = 0
        if current != 0:
            return current
        if signal_float > self._long_entry:
            return 1
        if self._short_entry is not None and signal_float < self._short_entry:
            return -1
        return 0

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

        common_tickers = [c for c in signal_panel.columns if c in prices.columns]
        if not common_tickers:
            return pd.DataFrame(0.0, index=signal_panel.index, columns=prices.columns)
        aligned_signals = signal_panel[common_tickers]
        prices_aligned = prices.reindex(
            index=aligned_signals.index, columns=aligned_signals.columns
        )

        # Per-ticker state machine. positions[ticker] in {-1, 0, +1}.
        positions: dict[str, int] = dict.fromkeys(common_tickers, 0)
        weights = pd.DataFrame(
            0.0, index=aligned_signals.index, columns=prices.columns, dtype="float64"
        )

        for date_idx, signal_row in aligned_signals.iterrows():
            # pandas-stubs models .loc[Hashable] as ambiguous (Series | DataFrame);
            # at runtime a single-label lookup always yields a Series.
            price_row = cast("pd.Series", prices_aligned.loc[date_idx])  # type: ignore[call-overload]
            for ticker in common_tickers:
                signal_value = signal_row[ticker]
                price_value = price_row[ticker]
                # Out-of-universe guard: NaN price → ticker not tradeable today.
                # NaN signal: hold current position. Both branches: skip update.
                if pd.isna(price_value) or pd.isna(signal_value):
                    continue
                positions[ticker] = self._next_position(positions[ticker], float(signal_value))

            # Compute raw weights for this date.
            for ticker, sign in positions.items():
                if sign != 0:
                    weights.at[date_idx, ticker] = sign * self._position_size

            # Apply max_positions cap by keeping the strongest |signal| values.
            if self._max_positions is not None:
                self._enforce_max_positions(date_idx, weights, aligned_signals, positions)

        scaled = scale_to_gross(weights, target_gross=self._target_gross)
        return apply_position_persistence(
            scaled,
            smoothing_alpha=self._smoothing_alpha,
            max_turnover_annual=self._max_turnover_annual,
        )

    def _enforce_max_positions(
        self,
        date_idx: Hashable,
        weights: pd.DataFrame,
        aligned_signals: pd.DataFrame,
        positions: dict[str, int],
    ) -> None:
        """Cap held position count at `self._max_positions` by |signal| rank."""
        if self._max_positions is None:
            return
        held_row = cast("pd.Series", weights.loc[date_idx])  # type: ignore[call-overload]
        held = [str(t) for t in held_row.index[held_row != 0]]
        if len(held) <= self._max_positions:
            return
        signal_row = cast("pd.Series", aligned_signals.loc[date_idx])  # type: ignore[call-overload]
        abs_signals = signal_row.reindex(held).abs()
        keep = set(abs_signals.nlargest(self._max_positions).index)
        drop = [t for t in held if t not in keep]
        weights.loc[date_idx, drop] = 0.0
        for t in drop:
            positions[t] = 0
