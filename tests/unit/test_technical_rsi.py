"""Unit tests for RsiSignal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.technical.rsi import RsiSignal


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
    def test_default_window_14(self) -> None:
        sig = RsiSignal()
        assert sig.signal_id == "rsi"
        assert sig._window == 14

    def test_invalid_window_raises(self) -> None:
        with pytest.raises(ValueError, match="window"):
            RsiSignal(window=1)


class TestCompute:
    def test_steady_uptrend_yields_high_rsi(self, tmp_path: Path) -> None:
        """Monotonically rising prices → loss is zero → RSI 100 → rescaled +1."""
        store = ParquetStore(tmp_path)
        _seed_prices(store, "AAA", [100.0 + i for i in range(60)], start=date(2024, 1, 1))

        sig = RsiSignal(window=14)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 4, 1)),
            date(2024, 1, 25),
            date(2024, 3, 25),
            ["AAA"],
        )
        non_null = panel["AAA"].dropna()
        assert len(non_null) > 0
        # With zero losses, RSI = 100, rescaled = +1.0
        for v in non_null.values:
            assert v == pytest.approx(1.0, abs=1e-6)

    def test_steady_downtrend_yields_low_rsi(self, tmp_path: Path) -> None:
        """Monotonically falling prices → gain is zero → RSI 0 → rescaled -1."""
        store = ParquetStore(tmp_path)
        _seed_prices(store, "AAA", [200.0 - i for i in range(60)], start=date(2024, 1, 1))

        sig = RsiSignal(window=14)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 4, 1)),
            date(2024, 1, 25),
            date(2024, 3, 25),
            ["AAA"],
        )
        non_null = panel["AAA"].dropna()
        # With zero gains, RSI = 0, rescaled = -1.0
        for v in non_null.values:
            assert v == pytest.approx(-1.0, abs=1e-6)

    def test_random_walk_centers_near_zero(self, tmp_path: Path) -> None:
        """A random walk has roughly equal gains and losses → RSI ~50 → rescaled ~0."""
        store = ParquetStore(tmp_path)
        rng = np.random.default_rng(seed=42)
        closes = [100.0]
        for _ in range(100):
            closes.append(closes[-1] * (1.0 + rng.normal(0.0, 0.01)))
        _seed_prices(store, "AAA", closes, start=date(2024, 1, 1))

        sig = RsiSignal(window=14)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 5, 31)),
            date(2024, 1, 25),
            date(2024, 5, 15),
            ["AAA"],
        )
        # Mean RSI over the whole window should be near zero on a zero-drift random walk.
        non_null = panel["AAA"].dropna()
        assert len(non_null) > 0
        mean_score = float(non_null.mean())
        assert abs(mean_score) < 0.3, f"random walk RSI should center near 0, got {mean_score}"

    def test_fingerprint_includes_window(self) -> None:
        a = RsiSignal(window=14)
        b = RsiSignal(window=21)
        assert a.fingerprint() != b.fingerprint()
