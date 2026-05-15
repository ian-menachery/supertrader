"""Unit tests for backtest.sector_decomp."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from supertrader.backtest.sector_decomp import (
    SectorContribution,
    decompose_by_sector,
    format_table,
    sector_lookup,
)
from supertrader.data.universe import StaticUniverse, UniverseEntry


@pytest.fixture
def two_sector_universe() -> StaticUniverse:
    return StaticUniverse(
        [
            UniverseEntry(ticker="AAA", name="A", sector="Tech", market_cap_usd=1e9, adv_usd=1e7),
            UniverseEntry(ticker="BBB", name="B", sector="Tech", market_cap_usd=1e9, adv_usd=1e7),
            UniverseEntry(ticker="CCC", name="C", sector="Energy", market_cap_usd=1e9, adv_usd=1e7),
            UniverseEntry(ticker="DDD", name="D", sector="Energy", market_cap_usd=1e9, adv_usd=1e7),
        ]
    )


def _build_inputs(
    *,
    n_days: int = 20,
    tickers: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD"),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range("2024-01-02", periods=n_days, freq="B", tz="UTC")
    weights = pd.DataFrame(0.25, index=idx, columns=list(tickers))
    # Random-walk prices with seeded drift
    rng = np.random.default_rng(42)
    rows: dict[str, list[float]] = {t: [100.0] for t in tickers}
    for _ in range(n_days - 1):
        for t in tickers:
            rows[t].append(rows[t][-1] * (1.0 + rng.normal(0.0, 0.01)))
    prices = pd.DataFrame(rows, index=idx)
    return weights, prices


class TestSectorLookup:
    def test_maps_ticker_to_sector(self, two_sector_universe: StaticUniverse) -> None:
        m = sector_lookup(two_sector_universe)
        assert m == {"AAA": "Tech", "BBB": "Tech", "CCC": "Energy", "DDD": "Energy"}


class TestDecomposeBySector:
    def test_empty_inputs_yield_empty_result(self) -> None:
        result = decompose_by_sector(
            weights=pd.DataFrame(),
            prices=pd.DataFrame(),
            ticker_to_sector={},
        )
        assert result == {}

    def test_sectors_keyed_in_output(self, two_sector_universe: StaticUniverse) -> None:
        weights, prices = _build_inputs()
        result = decompose_by_sector(
            weights=weights,
            prices=prices,
            ticker_to_sector=sector_lookup(two_sector_universe),
            execution_delay_bars=0,
        )
        assert set(result.keys()) == {"Tech", "Energy"}
        for sector, contrib in result.items():
            assert isinstance(contrib, SectorContribution)
            assert contrib.sector == sector
            assert contrib.n_tickers == 2

    def test_concentrated_returns_isolated_to_one_sector(self) -> None:
        """If only Tech tickers have positive returns, Tech's cum_return >> Energy's."""
        idx = pd.date_range("2024-01-02", periods=20, freq="B", tz="UTC")
        tickers = ["AAA", "BBB", "CCC", "DDD"]
        weights = pd.DataFrame(0.25, index=idx, columns=tickers)
        # Tech (AAA, BBB) climbs steadily; Energy (CCC, DDD) is flat.
        prices = pd.DataFrame(
            {
                "AAA": [100.0 + i for i in range(20)],
                "BBB": [100.0 + i for i in range(20)],
                "CCC": [100.0] * 20,
                "DDD": [100.0] * 20,
            },
            index=idx,
        )
        ticker_to_sector = {"AAA": "Tech", "BBB": "Tech", "CCC": "Energy", "DDD": "Energy"}
        result = decompose_by_sector(
            weights=weights,
            prices=prices,
            ticker_to_sector=ticker_to_sector,
            execution_delay_bars=0,
        )
        assert result["Tech"].cum_return > 0
        # Energy is flat → cumulative return is essentially zero.
        assert math.isclose(result["Energy"].cum_return, 0.0, abs_tol=1e-9)

    def test_zero_exposure_sector_omitted(self) -> None:
        """A sector that never has any weight allocated should NOT appear in output."""
        idx = pd.date_range("2024-01-02", periods=10, freq="B", tz="UTC")
        weights = pd.DataFrame({"AAA": 0.5, "BBB": 0.5, "CCC": 0.0, "DDD": 0.0}, index=idx)
        prices = pd.DataFrame(100.0, index=idx, columns=["AAA", "BBB", "CCC", "DDD"])
        ticker_to_sector = {"AAA": "Tech", "BBB": "Tech", "CCC": "Energy", "DDD": "Energy"}
        result = decompose_by_sector(
            weights=weights,
            prices=prices,
            ticker_to_sector=ticker_to_sector,
            execution_delay_bars=0,
        )
        assert "Tech" in result
        assert "Energy" not in result

    def test_unmapped_ticker_lands_in_unknown_sector(self) -> None:
        weights, prices = _build_inputs(tickers=("AAA",))
        # AAA has no mapping; should bucket as "Unknown" not be dropped silently
        result = decompose_by_sector(
            weights=weights,
            prices=prices,
            ticker_to_sector={},
            execution_delay_bars=0,
        )
        assert "Unknown" in result
        assert result["Unknown"].n_tickers == 1


class TestFormatTable:
    def test_empty_input(self) -> None:
        assert "no sector" in format_table({}).lower()

    def test_table_includes_each_sector(self) -> None:
        contribs = {
            "Tech": SectorContribution("Tech", 5, 0.20, 1.5, 1.8, -0.05, 0.5),
            "Energy": SectorContribution("Energy", 3, -0.10, -0.4, -0.3, -0.15, 0.3),
        }
        table = format_table(contribs)
        assert "Tech" in table
        assert "Energy" in table
        # Higher-return sector comes first
        tech_idx = table.find("Tech")
        energy_idx = table.find("Energy")
        assert tech_idx < energy_idx
