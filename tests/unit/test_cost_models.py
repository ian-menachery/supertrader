"""Tests for the cost / slippage / borrow models with hand-computed expected values."""

from __future__ import annotations

import math

import pytest

from supertrader.backtest.borrow import (
    DAYS_PER_YEAR,
    annual_rate,
    borrow_dollars,
    daily_rate,
)
from supertrader.backtest.costs import commission_dollars, commission_fraction
from supertrader.backtest.slippage import (
    slippage_bps,
    slippage_dollars,
    slippage_fraction,
)
from supertrader.config.schemas import CostsConfig


@pytest.fixture
def costs() -> CostsConfig:
    # All-default config: commission 1 bps, slippage base 3 bps, impact 10 bps,
    # easy-to-borrow 50 bps/yr, htb 500 bps/yr.
    return CostsConfig()


class TestCommission:
    def test_fraction_default_1bps(self, costs: CostsConfig) -> None:
        assert commission_fraction(costs) == pytest.approx(0.0001)

    def test_dollars_100k_at_1bps(self, costs: CostsConfig) -> None:
        # 1 bps of $100,000 = $10
        assert commission_dollars(100_000.0, costs) == pytest.approx(10.0)

    def test_dollars_absolute_value(self, costs: CostsConfig) -> None:
        # Negative notional (sell side) still charges positive commission
        assert commission_dollars(-100_000.0, costs) == pytest.approx(10.0)

    def test_zero_notional(self, costs: CostsConfig) -> None:
        assert commission_dollars(0.0, costs) == 0.0


class TestSlippageBps:
    def test_base_plus_zero_impact_at_tiny_notional(self, costs: CostsConfig) -> None:
        # $100 trade on $10M ADV: ratio 1e-5, sqrt = 0.00316
        # bps = 3 + 10 * 0.00316 = 3.0316
        result = slippage_bps(100.0, 10_000_000.0, costs)
        assert result == pytest.approx(3.0 + 10.0 * math.sqrt(100.0 / 10_000_000.0))

    def test_full_impact_at_100pct_adv(self, costs: CostsConfig) -> None:
        # Notional == ADV: ratio 1, sqrt 1
        # bps = 3 + 10 * 1 = 13
        result = slippage_bps(10_000_000.0, 10_000_000.0, costs)
        assert result == pytest.approx(13.0)

    def test_worst_case_when_adv_is_zero(self, costs: CostsConfig) -> None:
        # Unknown / zero ADV is treated as 100% of ADV
        assert slippage_bps(100_000.0, 0.0, costs) == pytest.approx(13.0)

    def test_worst_case_when_adv_negative(self, costs: CostsConfig) -> None:
        # Defensive: negative ADV is data error, fall to worst-case
        assert slippage_bps(100_000.0, -1.0, costs) == pytest.approx(13.0)


class TestSlippageDollars:
    def test_dollars_100k_at_3bps(self, costs: CostsConfig) -> None:
        # $100k on $10B ADV: ratio 1e-5, basically just the 3 bps base
        # slippage ~= 3.0 bps of $100k = $30
        result = slippage_dollars(100_000.0, 10_000_000_000.0, costs)
        # sqrt(1e-5) ≈ 0.00316, so bps ≈ 3.0316
        assert result == pytest.approx(100_000.0 * 3.0316 / 10_000.0, rel=1e-3)

    def test_fraction_matches_bps(self, costs: CostsConfig) -> None:
        bps = slippage_bps(50_000.0, 1_000_000.0, costs)
        frac = slippage_fraction(50_000.0, 1_000_000.0, costs)
        assert frac == pytest.approx(bps / 10_000.0)


class TestBorrow:
    def test_easy_annual_rate_50bps(self, costs: CostsConfig) -> None:
        assert annual_rate(costs) == pytest.approx(0.005)

    def test_htb_annual_rate_500bps(self, costs: CostsConfig) -> None:
        assert annual_rate(costs, hard_to_borrow=True) == pytest.approx(0.05)

    def test_daily_rate_easy(self, costs: CostsConfig) -> None:
        assert daily_rate(costs) == pytest.approx(0.005 / DAYS_PER_YEAR)

    def test_dollars_easy_30_days(self, costs: CostsConfig) -> None:
        # -$100k held 30 days at 50 bps/yr
        # cost = 100k * 0.005 / 365 * 30 = ~$41.10
        result = borrow_dollars(-100_000.0, 30, costs)
        expected = 100_000.0 * 0.005 / DAYS_PER_YEAR * 30.0
        assert result == pytest.approx(expected)

    def test_htb_30_days(self, costs: CostsConfig) -> None:
        # -$100k held 30 days at 500 bps/yr
        result = borrow_dollars(-100_000.0, 30, costs, hard_to_borrow=True)
        expected = 100_000.0 * 0.05 / DAYS_PER_YEAR * 30.0
        assert result == pytest.approx(expected)

    def test_long_position_no_borrow(self, costs: CostsConfig) -> None:
        # Positive notional = long position; no borrow cost
        assert borrow_dollars(100_000.0, 30, costs) == 0.0

    def test_zero_days(self, costs: CostsConfig) -> None:
        assert borrow_dollars(-100_000.0, 0, costs) == 0.0

    def test_negative_days(self, costs: CostsConfig) -> None:
        # Defensive: negative days_held yields zero cost
        assert borrow_dollars(-100_000.0, -5, costs) == 0.0


class TestCostsConfigOverride:
    def test_custom_commission(self) -> None:
        cfg = CostsConfig(commission_bps=5.0)
        assert commission_fraction(cfg) == pytest.approx(0.0005)
        assert commission_dollars(100_000.0, cfg) == pytest.approx(50.0)

    def test_custom_slippage_impact(self) -> None:
        cfg = CostsConfig(slippage_bps_base=2.0, slippage_impact_coeff_bps=20.0)
        # Notional = ADV: bps = 2 + 20 * 1 = 22
        assert slippage_bps(1_000_000.0, 1_000_000.0, cfg) == pytest.approx(22.0)
