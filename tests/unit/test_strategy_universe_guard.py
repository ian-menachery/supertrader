"""Out-of-universe leakage guard for the cross-sectional ranker.

Per the platform-honesty review (2026-05-14): every weight assigned on
date T must correspond to a ticker that was tradeable on date T. The
strategy uses NaN in `prices[T, ticker]` as the "not tradeable today"
signal — a PITUniverse would set that NaN at pipeline level when the
ticker is outside today's index membership.

The bug being pinned: `MeanReversionStrategy.target_positions` ranked
ALL tickers with non-NaN signal values, regardless of whether the
ticker had a price on that date. The cross-sectional ranking was
silently contaminated by names the strategy shouldn't have seen.

The fix: filter the per-row signal series by price availability before
ranking. NaN price ⇒ ticker is excluded from today's cross-section.
"""

from __future__ import annotations

import pandas as pd

from supertrader.strategies.mean_reversion import MeanReversionStrategy


def _make_inputs(
    *,
    n_days: int = 3,
    tickers: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD", "EEE"),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (signal_panel, prices) with deterministic shapes for testing."""
    idx = pd.date_range("2024-01-02", periods=n_days, freq="B", tz="UTC")
    # Signals: column N has value equal to its index (so ranking is unambiguous).
    signals = pd.DataFrame(
        {t: [float(i)] * n_days for i, t in enumerate(tickers)},
        index=idx,
    ).astype("float64")
    # Prices: all 100.0 (everyone tradeable every day).
    prices = pd.DataFrame(100.0, index=idx, columns=list(tickers))
    return signals, prices


class TestUniverseGuard:
    def test_nan_price_ticker_gets_zero_weight(self) -> None:
        """A ticker with NaN price on date T must receive zero weight on date T."""
        signals, prices = _make_inputs()
        # Ticker BBB is "not in universe" on the first date — NaN price.
        prices.iloc[0, prices.columns.get_loc("BBB")] = float("nan")

        strat = MeanReversionStrategy(signal_name="s", quantile=0.4, min_signal_observations=2)
        weights = strat.target_positions({"s": signals}, prices)

        # BBB had no price on the first row → weight must be zero there.
        assert weights.iloc[0]["BBB"] == 0.0, (
            "ticker with NaN price on date T should not receive a weight"
        )

    def test_nan_price_excluded_from_ranking_cross_section(self) -> None:
        """The cross-section used for ranking must NOT include NaN-price tickers.

        Setup: 5 tickers with signal values 0,1,2,3,4. Strategy uses quantile=0.4.
        With BBB excluded the cross-section is {AAA(0), CCC(2), DDD(3), EEE(4)},
        so cutoff = floor(4 * 0.4) = 1: AAA (lowest) is long, EEE (highest) is
        short, CCC and DDD are middle.

        Critically: BBB (signal=1, would have been bottom-2 if present) must
        get zero weight, not be ranked as the lowest among the original 5.
        """
        signals, prices = _make_inputs()
        # Knock out BBB on the first date by NaN-ing its price.
        prices.iloc[0, prices.columns.get_loc("BBB")] = float("nan")

        strat = MeanReversionStrategy(signal_name="s", quantile=0.4, min_signal_observations=2)
        weights = strat.target_positions({"s": signals}, prices)

        row0 = weights.iloc[0]
        # mean_reversion direction is default: long bottom, short top.
        assert row0["AAA"] > 0, "AAA (lowest signal among tradeable) should be long"
        assert row0["EEE"] < 0, "EEE (highest signal) should be short"
        assert row0["CCC"] == 0.0, "CCC is middle of the tradeable cross-section"
        assert row0["DDD"] == 0.0, "DDD is middle of the tradeable cross-section"
        assert row0["BBB"] == 0.0, "BBB had no price; must be excluded entirely"

    def test_all_dates_clean_when_prices_complete(self) -> None:
        """Sanity: when every price is present, no NaN handling kicks in."""
        signals, prices = _make_inputs()
        strat = MeanReversionStrategy(signal_name="s", quantile=0.4, min_signal_observations=2)
        weights = strat.target_positions({"s": signals}, prices)
        # No row should be all zeros; the strategy should always trade.
        nonzero_rows = (weights.abs().sum(axis=1) > 0).sum()
        assert nonzero_rows == len(weights), "expected every row to produce weights"

    def test_too_few_tradeable_yields_zero_row(self) -> None:
        """If only 1 ticker has a non-NaN price + non-null signal, the row is zero.

        Pins the existing `min_signal_observations` behavior under the new
        NaN-price filter — the count of tradeable observations is post-filter.
        """
        signals, prices = _make_inputs()
        # NaN-out all but AAA on the first date.
        for t in ("BBB", "CCC", "DDD", "EEE"):
            prices.iloc[0, prices.columns.get_loc(t)] = float("nan")

        strat = MeanReversionStrategy(signal_name="s", quantile=0.4, min_signal_observations=2)
        weights = strat.target_positions({"s": signals}, prices)
        # Only 1 tradeable ticker → below min_signal_observations → all zeros.
        assert (weights.iloc[0] == 0.0).all(), "thin cross-section should produce zero row"
