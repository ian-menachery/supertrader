"""Unit tests for MovingAverageCrossSignal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.technical.ma_cross import MovingAverageCrossSignal


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
    def test_default_20_50(self) -> None:
        sig = MovingAverageCrossSignal()
        assert sig.signal_id == "ma_cross"
        assert sig._fast == 20
        assert sig._slow == 50

    def test_fast_minimum(self) -> None:
        with pytest.raises(ValueError, match="fast_window"):
            MovingAverageCrossSignal(fast_window=1, slow_window=10)

    def test_slow_must_exceed_fast(self) -> None:
        with pytest.raises(ValueError, match="slow_window"):
            MovingAverageCrossSignal(fast_window=20, slow_window=20)


class TestCompute:
    def test_steady_uptrend_yields_positive_score(self, tmp_path: Path) -> None:
        """A monotonically increasing price series → fast MA above slow MA → positive."""
        store = ParquetStore(tmp_path)
        # 80 days linear up
        _seed_prices(store, "AAA", [100.0 + i for i in range(80)], start=date(2024, 1, 1))

        sig = MovingAverageCrossSignal(fast_window=5, slow_window=20)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 5, 1)),
            date(2024, 2, 15),
            date(2024, 4, 15),
            ["AAA"],
        )
        non_null = panel["AAA"].dropna()
        assert len(non_null) > 0
        assert (non_null > 0).all(), "uptrending series should produce positive ma_cross"

    def test_steady_downtrend_yields_negative_score(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        _seed_prices(store, "AAA", [200.0 - i for i in range(80)], start=date(2024, 1, 1))

        sig = MovingAverageCrossSignal(fast_window=5, slow_window=20)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 5, 1)),
            date(2024, 2, 15),
            date(2024, 4, 15),
            ["AAA"],
        )
        non_null = panel["AAA"].dropna()
        assert (non_null < 0).all(), "downtrending series should produce negative ma_cross"

    def test_flat_series_yields_zero(self, tmp_path: Path) -> None:
        """Constant prices → fast MA == slow MA → zero score."""
        store = ParquetStore(tmp_path)
        _seed_prices(store, "AAA", [100.0] * 80, start=date(2024, 1, 1))

        sig = MovingAverageCrossSignal(fast_window=5, slow_window=20)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 5, 1)),
            date(2024, 2, 15),
            date(2024, 4, 15),
            ["AAA"],
        )
        non_null = panel["AAA"].dropna()
        assert len(non_null) > 0
        for v in non_null.values:
            assert abs(v) < 1e-9, f"flat series should give zero, got {v}"

    def test_fingerprint_distinguishes_windows(self) -> None:
        a = MovingAverageCrossSignal(fast_window=20, slow_window=50)
        b = MovingAverageCrossSignal(fast_window=10, slow_window=30)
        assert a.fingerprint() != b.fingerprint()
