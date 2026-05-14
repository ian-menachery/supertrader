"""Tests for MeanReversionStrategy ranking + risk.scale_to_gross."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrader.config.registry import strategies
from supertrader.strategies.mean_reversion import MeanReversionStrategy
from supertrader.strategies.risk import scale_to_gross


@pytest.fixture
def universe_tickers() -> list[str]:
    return ["AAPL", "MSFT", "NVDA", "TSLA", "GME", "AMD", "AMC", "INTC", "META", "AMZN"]


@pytest.fixture
def signal_panel(universe_tickers: list[str]) -> pd.DataFrame:
    # 3 days, 10 tickers. Signal values designed so ranking is deterministic.
    idx = pd.date_range("2024-01-02", periods=3, freq="B", tz="UTC")
    data = np.tile(np.arange(10, dtype=float), (3, 1))  # cols 0..9 each day
    return pd.DataFrame(data, index=idx, columns=universe_tickers)


@pytest.fixture
def prices(universe_tickers: list[str]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=3, freq="B", tz="UTC")
    return pd.DataFrame(100.0, index=idx, columns=universe_tickers)


class TestRanking:
    def test_long_bottom_short_top(self, signal_panel: pd.DataFrame, prices: pd.DataFrame) -> None:
        strat = MeanReversionStrategy(signal_name="s", quantile=0.3)
        weights = strat.target_positions({"s": signal_panel}, prices)

        # Quantile=0.3 of 10 tickers = bottom 3 long, top 3 short.
        # Signal values 0..9; bottom 3 = AAPL/MSFT/NVDA, top 3 = INTC/META/AMZN.
        first_day = weights.iloc[0]
        assert first_day["AAPL"] > 0
        assert first_day["MSFT"] > 0
        assert first_day["NVDA"] > 0
        assert first_day["INTC"] < 0
        assert first_day["META"] < 0
        assert first_day["AMZN"] < 0
        # Middle four (TSLA, GME, AMD, AMC) get zero
        assert first_day["TSLA"] == 0
        assert first_day["GME"] == 0
        assert first_day["AMD"] == 0
        assert first_day["AMC"] == 0

    def test_weights_sum_to_zero(self, signal_panel: pd.DataFrame, prices: pd.DataFrame) -> None:
        strat = MeanReversionStrategy(signal_name="s")
        weights = strat.target_positions({"s": signal_panel}, prices)
        for _, row in weights.iterrows():
            assert float(row.sum()) == pytest.approx(0.0, abs=1e-10)

    def test_gross_exposure_normalized(
        self, signal_panel: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        strat = MeanReversionStrategy(signal_name="s", target_gross=1.0)
        weights = strat.target_positions({"s": signal_panel}, prices)
        for _, row in weights.iterrows():
            assert float(row.abs().sum()) == pytest.approx(1.0, abs=1e-10)

    def test_custom_target_gross(self, signal_panel: pd.DataFrame, prices: pd.DataFrame) -> None:
        strat = MeanReversionStrategy(signal_name="s", target_gross=2.0)
        weights = strat.target_positions({"s": signal_panel}, prices)
        for _, row in weights.iterrows():
            assert float(row.abs().sum()) == pytest.approx(2.0, abs=1e-10)


class TestThinCrossSection:
    def test_few_observations_yields_zero_row(
        self, prices: pd.DataFrame, universe_tickers: list[str]
    ) -> None:
        idx = pd.date_range("2024-01-02", periods=3, freq="B", tz="UTC")
        # Only 2 non-null tickers per row — under default min_obs=5
        sparse = pd.DataFrame(np.nan, index=idx, columns=universe_tickers)
        sparse["AAPL"] = 0.5
        sparse["MSFT"] = -0.5
        strat = MeanReversionStrategy(signal_name="s", min_signal_observations=5)
        weights = strat.target_positions({"s": sparse}, prices)
        # All zero
        assert (weights == 0).all().all()

    def test_zero_cutoff_yields_zero_row(self, prices: pd.DataFrame) -> None:
        # 3 tickers * quantile 0.3 = floor(0.9) = 0 -> no trades
        idx = pd.date_range("2024-01-02", periods=3, freq="B", tz="UTC")
        small_signal = pd.DataFrame(
            {"AAPL": [1.0, 2.0, 3.0], "MSFT": [-1.0, -2.0, -3.0], "NVDA": [0.0, 0.0, 0.0]},
            index=idx,
        )
        # Need quantile that yields cutoff=0; 0.3 * 3 = 0.9 → floor=0
        strat = MeanReversionStrategy(signal_name="s", quantile=0.3, min_signal_observations=2)
        weights = strat.target_positions({"s": small_signal}, prices.iloc[:, :3])
        # Rows that have cutoff=0 → all zeros
        assert (weights == 0).all().all()


class TestValidation:
    def test_invalid_quantile_raises(self) -> None:
        with pytest.raises(ValueError, match="quantile"):
            MeanReversionStrategy(signal_name="s", quantile=0.0)
        with pytest.raises(ValueError, match="quantile"):
            MeanReversionStrategy(signal_name="s", quantile=0.6)

    def test_invalid_min_obs_raises(self) -> None:
        with pytest.raises(ValueError, match="min_signal_observations"):
            MeanReversionStrategy(signal_name="s", min_signal_observations=1)

    def test_invalid_target_gross_raises(self) -> None:
        with pytest.raises(ValueError, match="target_gross"):
            MeanReversionStrategy(signal_name="s", target_gross=0.0)

    def test_missing_signal_raises(self, prices: pd.DataFrame) -> None:
        strat = MeanReversionStrategy(signal_name="missing")
        with pytest.raises(KeyError, match="missing"):
            strat.target_positions({"other": prices}, prices)


class TestRegistry:
    def test_registered(self) -> None:
        assert "mean_reversion" in strategies
        assert strategies.resolve("mean_reversion") is MeanReversionStrategy


class TestScaleToGross:
    def test_unit_gross(self) -> None:
        idx = pd.date_range("2024-01-02", periods=2, freq="B", tz="UTC")
        w = pd.DataFrame({"A": [0.3, 0.4], "B": [-0.3, -0.6]}, index=idx)
        scaled = scale_to_gross(w, target_gross=1.0)
        for _, row in scaled.iterrows():
            assert float(row.abs().sum()) == pytest.approx(1.0)

    def test_empty_row_stays_empty(self) -> None:
        idx = pd.date_range("2024-01-02", periods=2, freq="B", tz="UTC")
        w = pd.DataFrame({"A": [0.0, 0.5], "B": [0.0, -0.5]}, index=idx)
        scaled = scale_to_gross(w, target_gross=1.0)
        assert (scaled.iloc[0] == 0).all()
        assert float(scaled.iloc[1].abs().sum()) == pytest.approx(1.0)

    def test_empty_df(self) -> None:
        empty = pd.DataFrame()
        result = scale_to_gross(empty)
        assert result.empty
