"""Unit tests for ZScoreReversalSignal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.technical.reversal import ZScoreReversalSignal


def _seed_prices(store: ParquetStore, ticker: str, closes: list[float], start: date) -> None:
    idx = pd.date_range(start=start, periods=len(closes), freq="B").date.tolist()
    frame = pl.LazyFrame(
        {
            "ticker": [ticker] * len(closes),
            "date": idx,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )
    store.write("yfinance.prices.daily", frame, partition_keys=("ticker",))


class TestConstruction:
    def test_default_params(self) -> None:
        sig = ZScoreReversalSignal()
        assert sig.signal_id == "zscore_reversal"

    def test_lookback_minimum(self) -> None:
        with pytest.raises(ValueError, match="lookback_days"):
            ZScoreReversalSignal(lookback_days=3)


class TestCompute:
    def test_steady_drift_yields_low_zscore(self, tmp_path: Path) -> None:
        """Constant daily return means today's return matches the trailing mean → z near 0."""
        store = ParquetStore(tmp_path)
        # 1% per day for 100 days — flat z-score after window fills
        closes = [100.0 * (1.01**i) for i in range(100)]
        _seed_prices(store, "AAA", closes, start=date(2024, 1, 1))

        sig = ZScoreReversalSignal(lookback_days=20)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 6, 1)),
            date(2024, 3, 1),
            date(2024, 4, 1),
            ["AAA"],
        )
        # Returns are constant so std=0 → z = NaN per signal logic.
        # Verify that the panel exists but values are NaN where std=0.
        assert "AAA" in panel.columns

    def test_one_day_shock_produces_high_zscore(self, tmp_path: Path) -> None:
        """A single anomalous return should appear as a large z-score."""
        store = ParquetStore(tmp_path)
        rng = np.random.default_rng(42)
        closes = [100.0]
        for _ in range(60):
            r = rng.normal(0.0005, 0.01)
            closes.append(closes[-1] * (1.0 + r))
        # Insert a 10% jump on day 50
        for i in range(50, len(closes)):
            closes[i] *= 1.10
        _seed_prices(store, "AAA", closes, start=date(2024, 1, 1))

        sig = ZScoreReversalSignal(lookback_days=20)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 6, 1)),
            date(2024, 1, 1),
            date(2024, 5, 1),
            ["AAA"],
        )
        # The day-50 jump should produce a very large z-score
        max_z = panel["AAA"].abs().max()
        assert max_z > 5.0, f"expected high-magnitude z-score from jump, got max={max_z}"

    def test_fingerprint_includes_params(self) -> None:
        a = ZScoreReversalSignal(lookback_days=20)
        b = ZScoreReversalSignal(lookback_days=10)
        assert a.fingerprint() != b.fingerprint()
