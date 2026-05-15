"""Position-sizing and risk-overlay helpers.

v1 is intentionally minimal: `scale_to_gross` for gross-exposure rescaling
plus `apply_position_persistence` for EMA smoothing + per-day turnover
capping. Sector caps, position-size caps, and per-ticker exposure limits
are deferred to Phase 2 — the universe is small enough for v1 that crude
gross-exposure scaling is adequate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    pass

# 252 trading days/yr — matches `backtest.metrics.ANNUALIZATION_DAILY`.
# Used to translate `max_turnover_annual` caps into per-day budgets.
ANNUALIZATION_DAILY: int = 252


def scale_to_gross(weights: pd.DataFrame, target_gross: float = 1.0) -> pd.DataFrame:
    """Rescale each row so `sum(|w|)` equals `target_gross`.

    Rows that are entirely zero (or NaN) are left untouched.
    """
    if weights.empty:
        return weights.copy()
    w = weights.fillna(0.0).astype("float64")
    row_gross = w.abs().sum(axis=1)
    # Replace 0 with 1 to avoid div-by-zero; rows with sum 0 stay all-zero.
    safe = row_gross.where(row_gross > 0, 1.0)
    scaled = w.div(safe, axis=0) * target_gross
    # Restore exact zeros for empty rows
    empty_rows = row_gross == 0
    if empty_rows.any():
        scaled.loc[empty_rows, :] = 0.0
    return scaled


def apply_position_persistence(
    weights: pd.DataFrame,
    *,
    smoothing_alpha: float = 1.0,
    max_turnover_annual: float | None = None,
) -> pd.DataFrame:
    """Apply EMA smoothing + per-day turnover cap to a weight DataFrame.

    Both transforms are no-ops at default params. When set, they reduce
    day-to-day churn which (a) makes signals earn their cost before driving
    trades and (b) prevents silently-absurd turnover from leaving the
    strategy layer.

    EMA: `applied[t] = alpha * proposed[t] + (1 - alpha) * applied[t-1]`.

    Turnover cap: per-day turnover is `sum(|applied[t] - applied[t-1]|) / 2`.
    If the cap is set and binding, scale the per-day change pro-rata so the
    daily turnover equals the budget (`max_turnover_annual / 252`).

    Smoothing is applied before the turnover cap: today's intended position
    is the smoothed weight, and the cap then bounds how far we can move
    from yesterday.
    """
    if smoothing_alpha >= 1.0 and max_turnover_annual is None:
        return weights
    if not 0 < smoothing_alpha <= 1.0:
        msg = f"smoothing_alpha must be in (0, 1], got {smoothing_alpha}"
        raise ValueError(msg)
    if max_turnover_annual is not None and max_turnover_annual <= 0:
        msg = f"max_turnover_annual must be positive when set, got {max_turnover_annual}"
        raise ValueError(msg)
    daily_cap = (
        max_turnover_annual / ANNUALIZATION_DAILY if max_turnover_annual is not None else None
    )
    out = weights.copy()
    prev = pd.Series(0.0, index=weights.columns, dtype="float64")
    for date_idx in weights.index:
        proposed = weights.loc[date_idx].astype("float64")
        smoothed = smoothing_alpha * proposed + (1.0 - smoothing_alpha) * prev
        if daily_cap is not None:
            change = smoothed - prev
            proposed_daily_turnover = float(change.abs().sum()) / 2.0
            if proposed_daily_turnover > daily_cap:
                blend = daily_cap / proposed_daily_turnover
                smoothed = prev + change * blend
        out.loc[date_idx] = smoothed
        prev = smoothed
    return out
