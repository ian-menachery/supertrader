"""Sector decomposition of a backtest's daily returns.

Given a `BacktestResult` (target weights + price returns) and a universe
that exposes per-ticker sector metadata, this module computes:

  * Per-sector daily-return series — the slice of the strategy's PnL
    attributable to each sector (weight x ticker_return summed within
    sector).
  * Per-sector metrics (Sharpe, Sortino, MaxDD, cumulative return,
    average gross exposure).

This is a *re-analysis* helper — it doesn't run a new backtest. The
backtest is already done; we just slice the existing results by sector.
That's why it counts as zero new test-set peeks under ADR 0005.

Reuses `BacktestResult.weights` and `BacktestResult.equity_curve`. The
per-ticker returns are reconstructed from the price panel passed in;
this avoids needing the engine to surface per-ticker contributions
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from supertrader.backtest.metrics import (
    gross_exposure,
    max_drawdown,
    sharpe,
    sortino,
)

if TYPE_CHECKING:
    from supertrader.data.universe import StaticUniverse


@dataclass(frozen=True)
class SectorContribution:
    """Per-sector metrics for one slice of the backtest."""

    sector: str
    n_tickers: int
    cum_return: float
    sharpe: float
    sortino: float
    max_drawdown: float
    mean_gross_exposure: float


def sector_lookup(universe: StaticUniverse) -> dict[str, str]:
    """Build a `ticker → sector` dict from a `StaticUniverse`."""
    return {entry.ticker: entry.sector for entry in universe.entries()}


def per_ticker_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns from close prices. Same shape as `prices`."""
    return prices.pct_change()


def decompose_by_sector(
    weights: pd.DataFrame,
    prices: pd.DataFrame,
    ticker_to_sector: dict[str, str],
    *,
    execution_delay_bars: int = 1,
) -> dict[str, SectorContribution]:
    """Return per-sector metrics keyed by sector name.

    Args:
        weights: target weights `(date x ticker)`. Same shape as the
            strategy's `target_positions` output (after `scale_to_gross`).
        prices: close prices `(date x ticker)`.
        ticker_to_sector: ticker → sector mapping, typically from
            `sector_lookup(static_universe)`.
        execution_delay_bars: how many bars to shift the weights forward
            before computing returns. The engine uses 1 (signal at T,
            trade at T+1 open), so the default mirrors that.

    Returns:
        Dict keyed by sector name. Sectors with zero exposure throughout
        the window are omitted.

    """
    if weights.empty or prices.empty:
        return {}

    rets = per_ticker_returns(prices)
    # Align weights to prices index; shift by execution_delay_bars to mirror
    # the engine's order timing.
    aligned_w = weights.reindex(index=rets.index, columns=rets.columns).fillna(0.0)
    if execution_delay_bars > 0:
        aligned_w = aligned_w.shift(execution_delay_bars).fillna(0.0)

    # Per-ticker PnL contribution: weight x return.
    contrib = aligned_w * rets

    # Group columns by sector. Tickers with no sector mapping go into
    # "Unknown" so we don't silently drop them.
    sector_for = {t: ticker_to_sector.get(t, "Unknown") for t in contrib.columns}
    sectors = sorted(set(sector_for.values()))

    out: dict[str, SectorContribution] = {}
    for sector in sectors:
        cols = [t for t, s in sector_for.items() if s == sector]
        if not cols:
            continue
        sector_contrib = contrib[cols]
        sector_weights = aligned_w[cols]
        # Daily PnL for this sector = sum over its tickers.
        sector_daily = sector_contrib.sum(axis=1)
        # Skip sectors that never had any exposure.
        if (sector_weights.abs().sum(axis=1) == 0).all():
            continue
        cum_growth = (1.0 + sector_daily).prod()
        cum = float(cum_growth) - 1.0  # type: ignore[arg-type]
        out[sector] = SectorContribution(
            sector=sector,
            n_tickers=len(cols),
            cum_return=cum,
            sharpe=sharpe(sector_daily),
            sortino=sortino(sector_daily),
            max_drawdown=max_drawdown(sector_daily),
            mean_gross_exposure=gross_exposure(sector_weights),
        )
    return out


def format_table(contribs: dict[str, SectorContribution]) -> str:
    """Render a sector-decomposition dict as a fixed-width text table."""
    if not contribs:
        return "(no sector contributions)"
    rows = sorted(contribs.values(), key=lambda c: c.cum_return, reverse=True)
    header = (
        f"{'sector':<22} {'n':>3} {'cum_ret':>10} {'Sharpe':>8} "
        f"{'Sortino':>8} {'MaxDD':>10} {'gross':>8}"
    )
    lines = [header, "-" * len(header)]
    lines.extend(
        f"{r.sector:<22} {r.n_tickers:>3d} "
        f"{r.cum_return:>10.4f} {r.sharpe:>8.4f} "
        f"{r.sortino:>8.4f} {r.max_drawdown:>10.4f} "
        f"{r.mean_gross_exposure:>8.4f}"
        for r in rows
    )
    return "\n".join(lines)
