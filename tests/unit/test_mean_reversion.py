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

    def test_momentum_flips_long_and_short(
        self, signal_panel: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        """With direction='momentum' the long and short legs swap signs vs. mean-reversion."""
        mr = MeanReversionStrategy(signal_name="s", quantile=0.3, direction="mean_reversion")
        mom = MeanReversionStrategy(signal_name="s", quantile=0.3, direction="momentum")
        mr_w = mr.target_positions({"s": signal_panel}, prices)
        mom_w = mom.target_positions({"s": signal_panel}, prices)
        # Momentum is exactly the negation of mean-reversion on a deterministic ranking.
        for col in mr_w.columns:
            for idx in mr_w.index:
                assert mom_w.at[idx, col] == pytest.approx(-mr_w.at[idx, col], abs=1e-12)

    def test_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            MeanReversionStrategy(signal_name="s", direction="random")  # type: ignore[arg-type]

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


class TestSmoothing:
    """EMA weight-smoothing (P2 from the platform-honesty pass)."""

    def test_alpha_1_is_no_op(self, signal_panel: pd.DataFrame, prices: pd.DataFrame) -> None:
        baseline = MeanReversionStrategy(signal_name="s", quantile=0.3)
        smoothed = MeanReversionStrategy(signal_name="s", quantile=0.3, smoothing_alpha=1.0)
        w0 = baseline.target_positions({"s": signal_panel}, prices)
        w1 = smoothed.target_positions({"s": signal_panel}, prices)
        pd.testing.assert_frame_equal(w0, w1)

    def test_alpha_below_one_blends_toward_zero_on_day_one(
        self, signal_panel: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        """First-day weights start from zero, so alpha=0.5 halves the magnitudes."""
        strat = MeanReversionStrategy(signal_name="s", quantile=0.3, smoothing_alpha=0.5)
        weights = strat.target_positions({"s": signal_panel}, prices)
        baseline = MeanReversionStrategy(signal_name="s", quantile=0.3).target_positions(
            {"s": signal_panel}, prices
        )
        # On day 1: applied = 0.5 * baseline + 0.5 * 0 = 0.5 * baseline.
        pd.testing.assert_series_equal(weights.iloc[0], baseline.iloc[0] * 0.5, check_names=False)

    def test_alpha_below_one_reduces_gross_when_signal_flips(self) -> None:
        """A perfectly-flipping signal at alpha=0.5 should produce damped weights."""
        idx = pd.date_range("2024-01-02", periods=4, freq="B", tz="UTC")
        # Two days of "AAA=0, BBB=1" then two days of "AAA=1, BBB=0" — opposite ranking.
        signals = pd.DataFrame(
            {"AAA": [0.0, 0.0, 1.0, 1.0], "BBB": [1.0, 1.0, 0.0, 0.0]}, index=idx
        )
        prices = pd.DataFrame(100.0, index=idx, columns=["AAA", "BBB"])
        strat = MeanReversionStrategy(
            signal_name="s", quantile=0.5, min_signal_observations=2, smoothing_alpha=0.5
        )
        weights = strat.target_positions({"s": signals}, prices)
        # The flip-day weight magnitude should be smaller than the un-smoothed
        # baseline because EMA still carries half of yesterday's opposite stance.
        baseline = MeanReversionStrategy(
            signal_name="s", quantile=0.5, min_signal_observations=2
        ).target_positions({"s": signals}, prices)
        assert weights.abs().sum().sum() < baseline.abs().sum().sum()

    def test_invalid_alpha_raises(self) -> None:
        with pytest.raises(ValueError, match="smoothing_alpha"):
            MeanReversionStrategy(signal_name="s", smoothing_alpha=0.0)
        with pytest.raises(ValueError, match="smoothing_alpha"):
            MeanReversionStrategy(signal_name="s", smoothing_alpha=1.5)


class TestTurnoverCap:
    """Per-day turnover cap (P1 from the platform-honesty pass)."""

    def test_no_cap_is_default(self, signal_panel: pd.DataFrame, prices: pd.DataFrame) -> None:
        baseline = MeanReversionStrategy(signal_name="s", quantile=0.3)
        capped_none = MeanReversionStrategy(signal_name="s", quantile=0.3, max_turnover_annual=None)
        w0 = baseline.target_positions({"s": signal_panel}, prices)
        w1 = capped_none.target_positions({"s": signal_panel}, prices)
        pd.testing.assert_frame_equal(w0, w1)

    def test_low_cap_limits_per_day_turnover(self) -> None:
        """With a flipping signal, a low annual cap forces per-day turnover ≤ cap/252."""
        idx = pd.date_range("2024-01-02", periods=4, freq="B", tz="UTC")
        signals = pd.DataFrame(
            {"AAA": [0.0, 0.0, 1.0, 1.0], "BBB": [1.0, 1.0, 0.0, 0.0]}, index=idx
        )
        prices = pd.DataFrame(100.0, index=idx, columns=["AAA", "BBB"])
        # max_turnover_annual=50 → daily budget ≈ 0.198 (sum |delta_w| / 2 ≤ 0.198).
        strat = MeanReversionStrategy(
            signal_name="s",
            quantile=0.5,
            min_signal_observations=2,
            max_turnover_annual=50.0,
        )
        weights = strat.target_positions({"s": signals}, prices)
        # Compute per-day turnover and verify each row obeys the budget (with
        # a tiny float tolerance).
        daily_budget = 50.0 / 252.0
        deltas = weights.diff().abs().sum(axis=1) / 2.0
        for date_idx, t in deltas.iloc[1:].items():
            assert t <= daily_budget + 1e-9, (
                f"day {date_idx} exceeded daily turnover budget: {t:.4f} > {daily_budget:.4f}"
            )

    def test_high_cap_is_non_binding(
        self, signal_panel: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        """A cap large enough to never bind reproduces the un-capped weights."""
        baseline = MeanReversionStrategy(signal_name="s", quantile=0.3)
        capped = MeanReversionStrategy(signal_name="s", quantile=0.3, max_turnover_annual=10_000.0)
        w0 = baseline.target_positions({"s": signal_panel}, prices)
        w1 = capped.target_positions({"s": signal_panel}, prices)
        pd.testing.assert_frame_equal(w0, w1, atol=1e-12)

    def test_invalid_cap_raises(self) -> None:
        with pytest.raises(ValueError, match="max_turnover_annual"):
            MeanReversionStrategy(signal_name="s", max_turnover_annual=0.0)
        with pytest.raises(ValueError, match="max_turnover_annual"):
            MeanReversionStrategy(signal_name="s", max_turnover_annual=-5.0)
