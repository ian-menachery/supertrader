"""Unit tests for PercentChangeSignal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.technical.percent_change import PercentChangeSignal


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
    def test_default_is_1_day(self) -> None:
        sig = PercentChangeSignal()
        assert sig.signal_id == "percent_change"
        assert sig._lookback_days == 1

    def test_invalid_lookback_raises(self) -> None:
        with pytest.raises(ValueError, match="lookback_days"):
            PercentChangeSignal(lookback_days=0)


class TestCompute:
    def test_1day_pct_change_on_linear_prices(self, tmp_path: Path) -> None:
        """Linear price series 100, 102, 104, 106 → 2/100, 2/102, 2/104 ≈ constant pct."""
        store = ParquetStore(tmp_path)
        _seed_prices(store, "AAA", [100.0, 102.0, 104.0, 106.0, 108.0], start=date(2024, 1, 1))

        sig = PercentChangeSignal(lookback_days=1)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 1, 8)),
            date(2024, 1, 1),
            date(2024, 1, 5),
            ["AAA"],
        )
        non_null = panel["AAA"].dropna()
        # First value is NaN (no prior close); subsequent values are ~0.02 each.
        assert len(non_null) >= 3
        for v in non_null.values:
            assert 0.018 < v < 0.022, f"expected ~2% pct change, got {v}"

    def test_drop_2pct_produces_negative_signal(self, tmp_path: Path) -> None:
        """A -2% day should appear as score ≈ -0.02 — the actionable case."""
        store = ParquetStore(tmp_path)
        # Flat then a 2% drop
        _seed_prices(store, "AAA", [100.0, 100.0, 100.0, 98.0, 98.0], start=date(2024, 1, 1))

        sig = PercentChangeSignal(lookback_days=1)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 1, 8)),
            date(2024, 1, 1),
            date(2024, 1, 5),
            ["AAA"],
        )
        # Day where 100 → 98: -2%
        drops = panel["AAA"].dropna()
        assert (drops < -0.015).any(), "expected at least one -2% drop in the panel"

    def test_5day_lookback(self, tmp_path: Path) -> None:
        """5-day lookback compares today against close 5 trading days ago."""
        store = ParquetStore(tmp_path)
        # 10 days: linear 100..109
        _seed_prices(store, "AAA", [100.0 + i for i in range(10)], start=date(2024, 1, 1))

        sig = PercentChangeSignal(lookback_days=5)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 1, 15)),
            date(2024, 1, 1),
            date(2024, 1, 14),
            ["AAA"],
        )
        # Once 5 days of history exists, score ≈ 5/100 ≈ 0.05.
        non_null = panel["AAA"].dropna()
        assert len(non_null) > 0
        assert (non_null > 0.03).all(), "5-day pct change should be ~5% on a 100→105+ run"

    def test_fingerprint_includes_lookback(self) -> None:
        a = PercentChangeSignal(lookback_days=1)
        b = PercentChangeSignal(lookback_days=5)
        assert a.fingerprint() != b.fingerprint()
