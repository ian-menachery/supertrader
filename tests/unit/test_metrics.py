"""Tests for backtest/metrics.py — each metric against a hand-crafted return series."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from supertrader.backtest.metrics import (
    beta_to_benchmark,
    calmar,
    compute_metrics,
    gross_exposure,
    hit_rate,
    information_ratio,
    max_drawdown,
    net_exposure,
    profit_factor,
    sharpe,
    sortino,
    turnover,
)


@pytest.fixture
def simple_returns() -> pd.Series:
    # 5 days: 4 up + 1 down. Mean = (0.01 - 0.005 + 0.02 - 0.01 + 0.015) / 5 = 0.006
    idx = pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC")
    return pd.Series([0.01, -0.005, 0.02, -0.01, 0.015], index=idx, name="returns")


@pytest.fixture
def all_positive() -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC")
    return pd.Series([0.01, 0.02, 0.005, 0.015, 0.01], index=idx)


@pytest.fixture
def all_zero() -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC")
    return pd.Series([0.0] * 5, index=idx)


@pytest.fixture
def benchmark() -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC")
    return pd.Series([0.005, -0.002, 0.01, -0.005, 0.008], index=idx)


class TestSharpe:
    def test_known_value(self, simple_returns: pd.Series) -> None:
        # mean = 0.006; sample std with ddof=1
        mean = 0.006
        std = float(simple_returns.std(ddof=1))
        expected = mean / std * math.sqrt(252)
        assert sharpe(simple_returns) == pytest.approx(expected)

    def test_empty_returns_is_nan(self) -> None:
        empty = pd.Series(dtype="float64")
        assert math.isnan(sharpe(empty))

    def test_zero_volatility_yields_nan(self, all_zero: pd.Series) -> None:
        assert math.isnan(sharpe(all_zero))


class TestSortino:
    def test_known_value(self, simple_returns: pd.Series) -> None:
        mean = 0.006
        # Downside returns: -0.005 and -0.01
        downside_var = ((-0.005) ** 2 + (-0.01) ** 2) / 2
        downside_std = math.sqrt(downside_var)
        expected = mean / downside_std * math.sqrt(252)
        assert sortino(simple_returns) == pytest.approx(expected)

    def test_no_downside_returns_inf(self, all_positive: pd.Series) -> None:
        assert math.isinf(sortino(all_positive))


class TestMaxDrawdown:
    def test_hand_computed(self, simple_returns: pd.Series) -> None:
        # Cumulative wealth: 1.01, 1.00495, 1.0250490, 1.014798, 1.030020...
        # Running max: same as cumulative since each new high
        # Drawdown from peak 1.01 on day 2: 1.00495 / 1.01 - 1 = -0.005
        # On day 4 from peak 1.025049 on day 3: 1.014798 / 1.025049 - 1 ≈ -0.01
        cum = (1.0 + simple_returns).cumprod()
        running_max = cum.cummax()
        expected = float((cum / running_max - 1.0).min())
        assert max_drawdown(simple_returns) == pytest.approx(expected)
        assert max_drawdown(simple_returns) < 0

    def test_all_positive_zero_drawdown(self, all_positive: pd.Series) -> None:
        assert max_drawdown(all_positive) == 0.0

    def test_empty_returns_zero(self) -> None:
        assert max_drawdown(pd.Series(dtype="float64")) == 0.0


class TestCalmar:
    def test_positive_when_returns_positive(self, simple_returns: pd.Series) -> None:
        result = calmar(simple_returns)
        # Should be positive: positive cumulative return / positive |mdd|
        assert result > 0

    def test_undefined_when_no_drawdown(self, all_positive: pd.Series) -> None:
        assert math.isnan(calmar(all_positive))


class TestHitRate:
    def test_3_of_5(self, simple_returns: pd.Series) -> None:
        # 3 positive (0.01, 0.02, 0.015), 2 negative
        assert hit_rate(simple_returns) == pytest.approx(0.6)

    def test_all_zero_is_nan(self, all_zero: pd.Series) -> None:
        assert math.isnan(hit_rate(all_zero))

    def test_all_positive_one(self, all_positive: pd.Series) -> None:
        assert hit_rate(all_positive) == 1.0


class TestProfitFactor:
    def test_known_value(self, simple_returns: pd.Series) -> None:
        # Pos: 0.01 + 0.02 + 0.015 = 0.045
        # Neg: -0.005 - 0.01 = -0.015
        # PF = 0.045 / 0.015 = 3.0
        assert profit_factor(simple_returns) == pytest.approx(3.0)

    def test_all_positive_is_inf(self, all_positive: pd.Series) -> None:
        assert math.isinf(profit_factor(all_positive))


class TestTurnover:
    def test_daily_two_day_change(self) -> None:
        # Day 1: AAPL=0.5, MSFT=0.5
        # Day 2: AAPL=0.6, MSFT=0.4
        # Delta = |0.1| + |-0.1| = 0.2; /2 = 0.1
        idx = pd.date_range("2024-01-01", periods=2, freq="B", tz="UTC")
        w = pd.DataFrame({"AAPL": [0.5, 0.6], "MSFT": [0.5, 0.4]}, index=idx)
        # Only 1 delta (the 2->1 difference), so mean across non-first rows = 0.1
        assert turnover(w) == pytest.approx(0.1)

    def test_annualized(self) -> None:
        idx = pd.date_range("2024-01-01", periods=2, freq="B", tz="UTC")
        w = pd.DataFrame({"AAPL": [0.5, 0.6], "MSFT": [0.5, 0.4]}, index=idx)
        assert turnover(w, annualize=True) == pytest.approx(0.1 * 252)

    def test_empty_zero(self) -> None:
        empty = pd.DataFrame()
        assert turnover(empty) == 0.0


class TestExposure:
    def test_gross_average(self) -> None:
        # Day 1: 0.6 + 0.3 = 0.9; Day 2: 0.5 + 0.5 = 1.0; mean = 0.95
        idx = pd.date_range("2024-01-01", periods=2, freq="B", tz="UTC")
        w = pd.DataFrame({"AAPL": [0.6, 0.5], "MSFT": [0.3, 0.5]}, index=idx)
        assert gross_exposure(w) == pytest.approx(0.95)

    def test_gross_with_shorts(self) -> None:
        # Day 1: |0.6| + |-0.3| = 0.9
        idx = pd.date_range("2024-01-01", periods=1, freq="B", tz="UTC")
        w = pd.DataFrame({"AAPL": [0.6], "MSFT": [-0.3]}, index=idx)
        assert gross_exposure(w) == pytest.approx(0.9)

    def test_net_with_shorts(self) -> None:
        idx = pd.date_range("2024-01-01", periods=1, freq="B", tz="UTC")
        w = pd.DataFrame({"AAPL": [0.6], "MSFT": [-0.3]}, index=idx)
        assert net_exposure(w) == pytest.approx(0.3)


class TestBetaAndIR:
    def test_beta_against_self_is_one(self, simple_returns: pd.Series) -> None:
        assert beta_to_benchmark(simple_returns, simple_returns) == pytest.approx(1.0)

    def test_beta_against_scaled_self(self, simple_returns: pd.Series) -> None:
        # r = 2 * benchmark -> beta = 2.0
        scaled = simple_returns * 2.0
        assert beta_to_benchmark(scaled, simple_returns) == pytest.approx(2.0)

    def test_ir_against_self_is_nan(self, simple_returns: pd.Series) -> None:
        # Active = r - r = 0 -> std = 0 -> NaN
        assert math.isnan(information_ratio(simple_returns, simple_returns))

    def test_ir_known(self, simple_returns: pd.Series, benchmark: pd.Series) -> None:
        active = simple_returns - benchmark
        expected = float(active.mean()) / float(active.std(ddof=1)) * math.sqrt(252)
        assert information_ratio(simple_returns, benchmark) == pytest.approx(expected)


class TestComputeMetrics:
    def test_returns_only(self, simple_returns: pd.Series) -> None:
        m = compute_metrics(simple_returns)
        assert set(m.keys()) == {
            "sharpe",
            "sortino",
            "max_drawdown",
            "calmar",
            "hit_rate",
            "profit_factor",
        }
        assert m["hit_rate"] == pytest.approx(0.6)

    def test_with_weights(self, simple_returns: pd.Series) -> None:
        idx = simple_returns.index
        w = pd.DataFrame({"AAPL": [0.5] * 5, "MSFT": [0.5] * 5}, index=idx)
        m = compute_metrics(simple_returns, weights=w)
        assert "turnover_daily" in m
        assert "gross_exposure" in m
        assert m["gross_exposure"] == pytest.approx(1.0)

    def test_with_benchmark(self, simple_returns: pd.Series, benchmark: pd.Series) -> None:
        m = compute_metrics(simple_returns, benchmark_returns=benchmark)
        assert "beta_to_benchmark" in m
        assert "information_ratio" in m


class TestNumericalStability:
    def test_nan_handling(self) -> None:
        # Returns with NaN — should be dropped before computation
        idx = pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC")
        r = pd.Series([0.01, np.nan, 0.02, -0.01, 0.015], index=idx)
        result = sharpe(r)
        # Mean of [0.01, 0.02, -0.01, 0.015] = 0.00875
        clean = r.dropna()
        expected = float(clean.mean()) / float(clean.std(ddof=1)) * math.sqrt(252)
        assert result == pytest.approx(expected)
