"""VectorbtEngine: turn target weights into a `Portfolio` and emit a BacktestResult.

vectorbt's `Portfolio.from_orders` with `size_type=TargetPercent` accepts per-cell
target weights directly. We use that path. The strategy → execution boundary is
pandas (vectorbt is pandas-native); the rest of the framework is Polars.

Borrow cost is applied as a post-process deduction from the daily-return series:
vectorbt-free does not model borrow natively, and reimplementing it inside the
engine is more work than just subtracting `daily_rate * |short_notional| / NAV`
after the fact. Documented loudly here so the next reader sees why borrow shows
up outside the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import vectorbt as vbt

from supertrader.backtest.borrow import daily_rate
from supertrader.backtest.costs import commission_fraction
from supertrader.backtest.metrics import compute_metrics

if TYPE_CHECKING:
    from supertrader.config.schemas import CostsConfig


@dataclass(frozen=True)
class BacktestResult:
    """Output of a single backtest run."""

    equity_curve: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    metrics: dict[str, float]
    initial_capital: float
    portfolio: Any = field(default=None, metadata={"doc": "underlying vbt.Portfolio"})


class VectorbtEngine:
    """Thin wrapper around `vbt.Portfolio.from_orders` with our cost model wiring."""

    def __init__(self, costs: CostsConfig) -> None:
        self.costs = costs

    def run(
        self,
        target_weights: pd.DataFrame,
        prices: pd.DataFrame,
        *,
        initial_capital: float = 1_000_000.0,
        execution_delay_bars: int = 1,
        benchmark_returns: pd.Series | None = None,
    ) -> BacktestResult:
        if target_weights.empty:
            msg = "target_weights is empty"
            raise ValueError(msg)
        if prices.empty:
            msg = "prices is empty"
            raise ValueError(msg)

        weights = self._align_weights(target_weights, prices, execution_delay_bars)

        commission = commission_fraction(self.costs)
        slippage = float(self.costs.slippage_bps_base) / 10_000.0

        portfolio = vbt.Portfolio.from_orders(
            close=prices,
            size=weights,
            size_type=vbt.portfolio.enums.SizeType.TargetPercent,
            direction=vbt.portfolio.enums.Direction.Both,
            fees=commission,
            slippage=slippage,
            init_cash=initial_capital,
            cash_sharing=True,
            call_seq="auto",
        )

        raw_returns: pd.Series = portfolio.returns()
        equity_curve: pd.Series = portfolio.value()

        # Apply borrow cost as a post-process deduction on shorts.
        borrow_drag = self._compute_borrow_drag(weights, prices, equity_curve)
        returns = raw_returns - borrow_drag

        metrics = compute_metrics(returns, weights=weights, benchmark_returns=benchmark_returns)

        return BacktestResult(
            equity_curve=equity_curve,
            returns=returns,
            weights=weights,
            metrics=dict(metrics),
            initial_capital=initial_capital,
            portfolio=portfolio,
        )

    @staticmethod
    def _align_weights(
        target_weights: pd.DataFrame,
        prices: pd.DataFrame,
        execution_delay_bars: int,
    ) -> pd.DataFrame:
        """Project target weights onto the price index, forward-fill, and apply delay."""
        # Drop tz on price index for shape alignment; carry tz info if compatible.
        weights = target_weights.copy()
        w_idx = pd.DatetimeIndex(weights.index)
        p_idx = pd.DatetimeIndex(prices.index)
        if w_idx.tz is None and p_idx.tz is not None:
            weights.index = w_idx.tz_localize(p_idx.tz)
        elif w_idx.tz is not None and p_idx.tz is None:
            weights.index = w_idx.tz_localize(None)

        # Reindex to price dates; forward-fill weights across non-signal days
        weights = weights.reindex(prices.index).ffill().fillna(0.0)

        # Restrict to ticker columns that exist in both
        common = [c for c in prices.columns if c in weights.columns]
        weights = weights[common].reindex(columns=prices.columns).fillna(0.0)

        if execution_delay_bars > 0:
            weights = weights.shift(execution_delay_bars).fillna(0.0)
        return weights

    def _compute_borrow_drag(
        self,
        weights: pd.DataFrame,
        prices: pd.DataFrame,
        equity_curve: pd.Series,
    ) -> pd.Series:
        """Daily borrow drag = daily_rate * |short_notional| / lagged_NAV."""
        # short_notional_t = sum over i of max(0, -w_{t,i}) * NAV_t  (approx via price proxy)
        short_weights = weights.clip(upper=0).abs()
        # Approximate short notional by NAV * |short weight| (gross-relative interpretation).
        # This sidesteps needing share counts; for cross-sectional small longs/shorts the
        # approximation is acceptable. Phase 2: replace with portfolio.positions().abs() per row.
        del prices  # informational only here; share-level borrow is Phase 2
        nav_lag = equity_curve.shift(1).fillna(equity_curve.iloc[0])
        short_notional = short_weights.sum(axis=1) * nav_lag
        rate = daily_rate(self.costs)
        # Return as fraction of NAV (a daily return drag)
        drag = (short_notional * rate) / nav_lag.replace(0, np.nan)
        return drag.fillna(0.0)
