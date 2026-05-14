"""Tests for VectorbtEngine — wraps vectorbt with cost wiring and borrow drag."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrader.backtest.engine import BacktestResult, VectorbtEngine
from supertrader.config.schemas import CostsConfig


@pytest.fixture
def zero_cost() -> CostsConfig:
    return CostsConfig(
        commission_bps=0.0,
        slippage_bps_base=0.0,
        slippage_impact_coeff_bps=0.0,
        borrow_bps_annual=0.0,
        hard_to_borrow_bps_annual=0.0,
    )


@pytest.fixture
def prices() -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=5, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "AAPL": [100.0, 101.0, 102.0, 101.0, 103.0],
            "MSFT": [200.0, 201.0, 199.0, 202.0, 203.0],
        },
        index=idx,
    )


@pytest.fixture
def long_only_weights(prices: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {"AAPL": [0.5] * 5, "MSFT": [0.5] * 5},
        index=prices.index,
    )


class TestRun:
    def test_run_returns_backtest_result(
        self, zero_cost: CostsConfig, long_only_weights: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        engine = VectorbtEngine(zero_cost)
        result = engine.run(long_only_weights, prices, execution_delay_bars=0)
        assert isinstance(result, BacktestResult)
        assert len(result.equity_curve) == len(prices)
        assert len(result.returns) == len(prices)
        assert result.weights.shape == prices.shape

    def test_equity_curve_grows_with_positive_returns(
        self, zero_cost: CostsConfig, long_only_weights: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        engine = VectorbtEngine(zero_cost)
        result = engine.run(long_only_weights, prices, execution_delay_bars=0)
        # Both tickers end higher; long-only should profit
        assert result.equity_curve.iloc[-1] > result.equity_curve.iloc[0]

    def test_commission_reduces_returns(
        self, long_only_weights: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        zero = CostsConfig(
            commission_bps=0.0,
            slippage_bps_base=0.0,
            slippage_impact_coeff_bps=0.0,
        )
        high = CostsConfig(
            commission_bps=50.0,  # 50 bps round-trip — very high
            slippage_bps_base=0.0,
            slippage_impact_coeff_bps=0.0,
        )
        r_zero = VectorbtEngine(zero).run(long_only_weights, prices, execution_delay_bars=0)
        r_high = VectorbtEngine(high).run(long_only_weights, prices, execution_delay_bars=0)
        assert r_high.equity_curve.iloc[-1] < r_zero.equity_curve.iloc[-1]

    def test_execution_delay_shifts_fills(
        self, zero_cost: CostsConfig, prices: pd.DataFrame
    ) -> None:
        # Allocate only on day 0; no further changes
        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        weights.iloc[0] = [0.5, 0.5]
        engine = VectorbtEngine(zero_cost)
        r0 = engine.run(weights, prices, execution_delay_bars=0)
        r1 = engine.run(weights, prices, execution_delay_bars=1)
        # delay=0 fills on day 0, delay=1 fills on day 1 → different equity paths
        assert r0.equity_curve.iloc[0] != r1.equity_curve.iloc[0] or (
            r0.equity_curve.iloc[-1] != r1.equity_curve.iloc[-1]
        )


class TestBorrowDrag:
    def test_short_position_incurs_drag(self, prices: pd.DataFrame) -> None:
        # Short both tickers — borrow drag should reduce returns vs zero borrow
        weights = pd.DataFrame(
            {"AAPL": [-0.5] * 5, "MSFT": [-0.5] * 5}, index=prices.index
        )
        no_borrow = CostsConfig(
            commission_bps=0.0,
            slippage_bps_base=0.0,
            slippage_impact_coeff_bps=0.0,
            borrow_bps_annual=0.0,
        )
        with_borrow = CostsConfig(
            commission_bps=0.0,
            slippage_bps_base=0.0,
            slippage_impact_coeff_bps=0.0,
            borrow_bps_annual=1000.0,  # 10%/yr — very high
        )
        r_no = VectorbtEngine(no_borrow).run(weights, prices, execution_delay_bars=0)
        r_with = VectorbtEngine(with_borrow).run(weights, prices, execution_delay_bars=0)
        # Borrow drag pulls daily returns down
        assert r_with.returns.sum() < r_no.returns.sum()

    def test_long_only_has_zero_borrow_drag(
        self, long_only_weights: pd.DataFrame, prices: pd.DataFrame
    ) -> None:
        with_borrow = CostsConfig(
            commission_bps=0.0,
            slippage_bps_base=0.0,
            slippage_impact_coeff_bps=0.0,
            borrow_bps_annual=1000.0,
        )
        r_borrow = VectorbtEngine(with_borrow).run(
            long_only_weights, prices, execution_delay_bars=0
        )
        no_borrow = CostsConfig(
            commission_bps=0.0,
            slippage_bps_base=0.0,
            slippage_impact_coeff_bps=0.0,
            borrow_bps_annual=0.0,
        )
        r_zero = VectorbtEngine(no_borrow).run(long_only_weights, prices, execution_delay_bars=0)
        # Long-only: no shorts, so borrow_drag is zero on every day
        np.testing.assert_array_almost_equal(
            r_borrow.returns.to_numpy(), r_zero.returns.to_numpy()
        )


class TestAlignment:
    def test_weights_reindexed_to_price_dates(
        self, zero_cost: CostsConfig, prices: pd.DataFrame
    ) -> None:
        # Weights defined only on day 1 — engine forward-fills
        idx = pd.date_range("2024-01-03", periods=1, freq="B", tz="UTC")
        sparse_weights = pd.DataFrame({"AAPL": [0.5], "MSFT": [0.5]}, index=idx)
        engine = VectorbtEngine(zero_cost)
        result = engine.run(sparse_weights, prices, execution_delay_bars=0)
        assert len(result.weights) == len(prices)

    def test_missing_columns_get_zero(self, zero_cost: CostsConfig, prices: pd.DataFrame) -> None:
        # Weights only for AAPL; MSFT must end up at zero in the engine
        weights = pd.DataFrame({"AAPL": [0.5] * 5}, index=prices.index)
        engine = VectorbtEngine(zero_cost)
        result = engine.run(weights, prices, execution_delay_bars=0)
        assert (result.weights["MSFT"] == 0).all()


class TestErrors:
    def test_empty_weights_raises(self, zero_cost: CostsConfig, prices: pd.DataFrame) -> None:
        with pytest.raises(ValueError, match="target_weights"):
            VectorbtEngine(zero_cost).run(pd.DataFrame(), prices)

    def test_empty_prices_raises(
        self, zero_cost: CostsConfig, long_only_weights: pd.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="prices"):
            VectorbtEngine(zero_cost).run(long_only_weights, pd.DataFrame())
