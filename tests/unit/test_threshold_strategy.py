"""Unit tests for SignalThresholdStrategy."""

from __future__ import annotations

import pandas as pd
import pytest

from supertrader.config.registry import strategies
from supertrader.strategies.threshold import SignalThresholdStrategy


@pytest.fixture
def tickers() -> list[str]:
    return ["AAA", "BBB", "CCC", "DDD"]


@pytest.fixture
def prices(tickers: list[str]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=10, freq="B", tz="UTC")
    return pd.DataFrame(100.0, index=idx, columns=tickers)


class TestConstruction:
    def test_default_long_only(self) -> None:
        strat = SignalThresholdStrategy(signal_name="s", long_entry=0.5)
        assert strat.strategy_id == "signal_threshold"
        assert strat.required_signals == ("s",)

    def test_short_entry_must_be_below_long_entry(self) -> None:
        with pytest.raises(ValueError, match="short_entry"):
            SignalThresholdStrategy(signal_name="s", long_entry=0.5, short_entry=0.6)

    def test_invalid_position_size(self) -> None:
        with pytest.raises(ValueError, match="position_size"):
            SignalThresholdStrategy(signal_name="s", long_entry=0.5, position_size=0.0)

    def test_invalid_max_positions(self) -> None:
        with pytest.raises(ValueError, match="max_positions"):
            SignalThresholdStrategy(signal_name="s", long_entry=0.5, max_positions=0)


class TestLongOnlyTransitions:
    def test_entry_when_signal_crosses_long_entry(
        self, tickers: list[str], prices: pd.DataFrame
    ) -> None:
        """Signal starts below threshold then crosses → enter long on cross day."""
        idx = prices.index
        # AAA: 0.0 → 0.0 → 1.0 → 1.0 → ... (cross on day 2)
        signal = pd.DataFrame(0.0, index=idx, columns=tickers)
        signal.loc[signal.index[2:], "AAA"] = 1.0
        strat = SignalThresholdStrategy(signal_name="s", long_entry=0.5, exit_threshold=0.0)
        weights = strat.target_positions({"s": signal}, prices)
        # AAA flat on days 0-1, long on days 2+
        assert weights.iloc[0]["AAA"] == 0.0
        assert weights.iloc[1]["AAA"] == 0.0
        assert weights.iloc[2]["AAA"] > 0
        assert weights.iloc[-1]["AAA"] > 0

    def test_exit_when_signal_drops_below_exit_threshold(
        self, tickers: list[str], prices: pd.DataFrame
    ) -> None:
        idx = prices.index
        # AAA: 1.0 first three days then drops to -0.5
        signal = pd.DataFrame(1.0, index=idx, columns=tickers)
        signal.loc[signal.index[3:], "AAA"] = -0.5
        strat = SignalThresholdStrategy(signal_name="s", long_entry=0.5, exit_threshold=0.0)
        weights = strat.target_positions({"s": signal}, prices)
        # AAA long on day 0 (signal=1, above long_entry); flat from day 3 onward
        assert weights.iloc[0]["AAA"] > 0
        assert weights.iloc[2]["AAA"] > 0
        assert weights.iloc[3]["AAA"] == 0.0

    def test_long_only_ignores_negative_signal(
        self, tickers: list[str], prices: pd.DataFrame
    ) -> None:
        """With short_entry=None, deeply-negative signal must NOT enter short."""
        idx = prices.index
        signal = pd.DataFrame(-5.0, index=idx, columns=tickers)
        strat = SignalThresholdStrategy(signal_name="s", long_entry=0.5, short_entry=None)
        weights = strat.target_positions({"s": signal}, prices)
        assert (weights == 0).all().all()


class TestLongShortTransitions:
    def test_short_entry_below_threshold(self, tickers: list[str], prices: pd.DataFrame) -> None:
        idx = prices.index
        signal = pd.DataFrame(0.0, index=idx, columns=tickers)
        signal.loc[signal.index[2:], "AAA"] = -2.0
        strat = SignalThresholdStrategy(
            signal_name="s", long_entry=1.0, short_entry=-1.0, exit_threshold=0.0
        )
        weights = strat.target_positions({"s": signal}, prices)
        # Day 0-1 flat (signal 0), days 2+ short (signal -2 < -1)
        assert weights.iloc[1]["AAA"] == 0.0
        assert weights.iloc[2]["AAA"] < 0
        assert weights.iloc[-1]["AAA"] < 0


class TestExclusionGuards:
    def test_nan_price_blocks_entry(self, tickers: list[str], prices: pd.DataFrame) -> None:
        idx = prices.index
        signal = pd.DataFrame(1.0, index=idx, columns=tickers)  # everyone wants long
        prices_with_gap = prices.copy()
        prices_with_gap.iloc[0, prices_with_gap.columns.get_loc("AAA")] = float("nan")
        strat = SignalThresholdStrategy(signal_name="s", long_entry=0.5, exit_threshold=0.0)
        weights = strat.target_positions({"s": signal}, prices_with_gap)
        # AAA had NaN price on day 0 → no entry that day. Day 1+ enters.
        assert weights.iloc[0]["AAA"] == 0.0
        assert weights.iloc[1]["AAA"] > 0

    def test_nan_signal_holds_position(self, tickers: list[str], prices: pd.DataFrame) -> None:
        """NaN signal on a held day should preserve the position, not flatten it."""
        idx = prices.index
        signal = pd.DataFrame(1.0, index=idx, columns=tickers)
        signal.loc[signal.index[3], "AAA"] = float("nan")  # one NaN day after entry
        strat = SignalThresholdStrategy(signal_name="s", long_entry=0.5, exit_threshold=0.0)
        weights = strat.target_positions({"s": signal}, prices)
        # AAA entered long on day 0; on day 3 signal is NaN → hold (stay long).
        assert weights.iloc[3]["AAA"] > 0


class TestMaxPositions:
    def test_cap_keeps_strongest_signals(self, tickers: list[str], prices: pd.DataFrame) -> None:
        idx = prices.index
        # Four tickers all eligible to enter long, but cap to top 2 by |signal|.
        signal = pd.DataFrame({"AAA": 1.0, "BBB": 5.0, "CCC": 2.0, "DDD": 10.0}, index=idx)
        strat = SignalThresholdStrategy(
            signal_name="s", long_entry=0.5, exit_threshold=0.0, max_positions=2
        )
        weights = strat.target_positions({"s": signal}, prices)
        held = weights.iloc[0][weights.iloc[0] != 0].index.tolist()
        assert sorted(held) == ["BBB", "DDD"], (
            f"cap should keep the two strongest signals, got {held}"
        )


class TestScaleToGross:
    def test_weights_normalized_to_target(self, tickers: list[str], prices: pd.DataFrame) -> None:
        idx = prices.index
        signal = pd.DataFrame(1.0, index=idx, columns=tickers)
        strat = SignalThresholdStrategy(
            signal_name="s", long_entry=0.5, exit_threshold=0.0, target_gross=2.0
        )
        weights = strat.target_positions({"s": signal}, prices)
        for _, row in weights.iterrows():
            assert float(row.abs().sum()) == pytest.approx(2.0, abs=1e-10)


class TestRegistry:
    def test_registered(self) -> None:
        assert "signal_threshold" in strategies
        assert strategies.resolve("signal_threshold") is SignalThresholdStrategy


class TestSmoothingAndTurnoverCap:
    def test_smoothing_reduces_first_day_magnitude(
        self, tickers: list[str], prices: pd.DataFrame
    ) -> None:
        idx = prices.index
        signal = pd.DataFrame(1.0, index=idx, columns=tickers)
        baseline = SignalThresholdStrategy(
            signal_name="s", long_entry=0.5, exit_threshold=0.0
        ).target_positions({"s": signal}, prices)
        smoothed = SignalThresholdStrategy(
            signal_name="s", long_entry=0.5, exit_threshold=0.0, smoothing_alpha=0.5
        ).target_positions({"s": signal}, prices)
        # First day: smoothed = 0.5 * baseline (prev was 0)
        for col in baseline.columns:
            assert smoothed.iloc[0][col] == pytest.approx(baseline.iloc[0][col] * 0.5)


def _build_idx(periods: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-02", periods=periods, freq="B", tz="UTC")
