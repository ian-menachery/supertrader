"""Backtest metrics: pure functions on a daily-return Series.

All annualized metrics assume daily-frequency returns and 252 trading days per
year. Sub-daily Sharpe (Phase 3 if intraday) would require re-annualization at
the bar frequency.

A return series is a pandas Series of float64 with a DatetimeIndex. NaN values
are dropped before computation — callers should be aware that aligning to a
trading calendar produces no NaNs in well-formed input.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Mapping


ANNUALIZATION_DAILY: int = 252


def _clean(returns: pd.Series) -> pd.Series:
    """Drop NaN values and return as float64 numpy-backed Series."""
    return returns.dropna().astype("float64")


def sharpe(returns: pd.Series, annualization: int = ANNUALIZATION_DAILY) -> float:
    """Annualized Sharpe ratio: mean(r) / std(r) * sqrt(annualization)."""
    r = _clean(returns)
    if r.empty:
        return float("nan")
    std = float(r.std(ddof=1))
    if std == 0:
        return float("nan") if r.mean() == 0 else float("inf") * np.sign(r.mean())
    return float(r.mean()) / std * math.sqrt(annualization)


def sortino(returns: pd.Series, annualization: int = ANNUALIZATION_DAILY) -> float:
    """Annualized Sortino ratio: mean(r) / std(min(r, 0)) * sqrt(annualization)."""
    r = _clean(returns)
    if r.empty:
        return float("nan")
    downside = r[r < 0]
    if downside.empty:
        return float("inf") if r.mean() > 0 else float("nan")
    # Downside deviation uses semi-deviation: sqrt(mean(downside_returns^2))
    downside_std = float(math.sqrt((downside**2).mean()))
    if downside_std == 0:
        return float("nan")
    return float(r.mean()) / downside_std * math.sqrt(annualization)


def max_drawdown(returns: pd.Series) -> float:
    """Maximum drawdown as a negative number (e.g., -0.25 for 25% peak-to-trough)."""
    r = _clean(returns)
    if r.empty:
        return 0.0
    cum = (1.0 + r).cumprod()
    running_max = cum.cummax()
    drawdown = cum / running_max - 1.0
    return float(drawdown.min())


def calmar(returns: pd.Series, annualization: int = ANNUALIZATION_DAILY) -> float:
    """Annualized return / |max drawdown|. Undefined when MDD = 0."""
    r = _clean(returns)
    if r.empty:
        return float("nan")
    mdd = max_drawdown(r)
    if mdd == 0:
        return float("nan")
    cum_total = float(np.prod(1.0 + r.to_numpy()) - 1.0)
    n_periods = len(r)
    years = n_periods / annualization
    annualized = (1.0 + cum_total) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    return annualized / abs(mdd)


def hit_rate(returns: pd.Series) -> float:
    """Fraction of non-zero returns that are positive."""
    r = _clean(returns)
    nonzero = r[r != 0]
    if nonzero.empty:
        return float("nan")
    return float((nonzero > 0).sum()) / len(nonzero)


def profit_factor(returns: pd.Series) -> float:
    """Sum of positive returns divided by |sum of negative returns|."""
    r = _clean(returns)
    pos = float(r[r > 0].sum())
    neg = float(r[r < 0].sum())
    if neg == 0:
        return float("inf") if pos > 0 else float("nan")
    return pos / abs(neg)


def turnover(weights: pd.DataFrame, *, annualize: bool = False) -> float:
    """Average per-period turnover: sum(|delta_weights|) / 2, averaged over time.

    Returns daily turnover by default. Set `annualize=True` to multiply by 252.
    """
    if weights.empty or len(weights) < 2:
        return 0.0
    w = weights.fillna(0.0).astype("float64")
    deltas = w.diff().abs().sum(axis=1) / 2.0
    daily = float(deltas.iloc[1:].mean())
    return daily * ANNUALIZATION_DAILY if annualize else daily


def gross_exposure(weights: pd.DataFrame) -> float:
    """Average gross exposure = mean over t of sum_i |w_{t,i}|."""
    if weights.empty:
        return 0.0
    w = weights.fillna(0.0).astype("float64")
    return float(w.abs().sum(axis=1).mean())


def net_exposure(weights: pd.DataFrame) -> float:
    """Average net exposure = mean over t of sum_i w_{t,i}."""
    if weights.empty:
        return 0.0
    w = weights.fillna(0.0).astype("float64")
    return float(w.sum(axis=1).mean())


def beta_to_benchmark(returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """OLS slope of `returns` on `benchmark_returns`. Aligns on shared index."""
    r = _clean(returns)
    b = _clean(benchmark_returns)
    aligned = pd.concat([r, b], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return float("nan")
    r_arr = aligned.iloc[:, 0].to_numpy()
    b_arr = aligned.iloc[:, 1].to_numpy()
    var_b = float(np.var(b_arr, ddof=1))
    if var_b == 0:
        return float("nan")
    cov = float(np.cov(r_arr, b_arr, ddof=1)[0, 1])
    return cov / var_b


def information_ratio(
    returns: pd.Series,
    benchmark_returns: pd.Series,
    annualization: int = ANNUALIZATION_DAILY,
) -> float:
    """Annualized IR of active returns: mean(r-b) / std(r-b) * sqrt(annualization)."""
    aligned = pd.concat([_clean(returns), _clean(benchmark_returns)], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return float("nan")
    active = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    std = float(active.std(ddof=1))
    if std == 0:
        return float("nan")
    return float(active.mean()) / std * math.sqrt(annualization)


def compute_metrics(
    returns: pd.Series,
    weights: pd.DataFrame | None = None,
    benchmark_returns: pd.Series | None = None,
    *,
    annualization: int = ANNUALIZATION_DAILY,
) -> Mapping[str, float]:
    """Compute the full metrics suite. Benchmark and weights are optional.

    Returns a dict suitable for JSON serialization. Metrics depending on
    missing inputs (e.g., beta without a benchmark) yield NaN.
    """
    out: dict[str, float] = {
        "sharpe": sharpe(returns, annualization),
        "sortino": sortino(returns, annualization),
        "max_drawdown": max_drawdown(returns),
        "calmar": calmar(returns, annualization),
        "hit_rate": hit_rate(returns),
        "profit_factor": profit_factor(returns),
    }
    if weights is not None:
        out["turnover_daily"] = turnover(weights, annualize=False)
        out["turnover_annual"] = turnover(weights, annualize=True)
        out["gross_exposure"] = gross_exposure(weights)
        out["net_exposure"] = net_exposure(weights)
    if benchmark_returns is not None:
        out["beta_to_benchmark"] = beta_to_benchmark(returns, benchmark_returns)
        out["information_ratio"] = information_ratio(returns, benchmark_returns, annualization)
    return out
