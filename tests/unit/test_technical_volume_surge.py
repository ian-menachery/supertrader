"""Unit tests for VolumeSurgeSignal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.technical.volume_surge import VolumeSurgeSignal


def _seed_ohlcv(
    store: ParquetStore,
    ticker: str,
    closes: list[float],
    volumes: list[int],
    start: date,
) -> None:
    assert len(closes) == len(volumes)
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
            "volume": volumes,
        }
    )
    store.write("yfinance.prices.daily", frame, partition_keys=("ticker",))


class TestConstruction:
    def test_threshold_must_exceed_one(self) -> None:
        with pytest.raises(ValueError, match="threshold"):
            VolumeSurgeSignal(abnormal_vol_threshold=1.0)

    def test_lookback_minimum(self) -> None:
        with pytest.raises(ValueError, match="lookback"):
            VolumeSurgeSignal(lookback_days=3)


class TestCompute:
    def test_quiet_market_produces_all_nan(self, tmp_path: Path) -> None:
        """Constant volume + flat returns → no events triggered."""
        store = ParquetStore(tmp_path)
        closes = [100.0] * 60
        volumes = [1_000_000] * 60
        _seed_ohlcv(store, "AAA", closes, volumes, start=date(2024, 1, 1))

        sig = VolumeSurgeSignal(lookback_days=20, abnormal_vol_threshold=2.0)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 4, 1)),
            date(2024, 2, 1),
            date(2024, 3, 22),
            ["AAA"],
        )
        assert panel["AAA"].isna().all()

    def test_volume_spike_with_positive_return_triggers(self, tmp_path: Path) -> None:
        """A 4x volume + positive return day produces a non-NaN score."""
        store = ParquetStore(tmp_path)
        # 40 baseline days: flat price, 1M volume
        # Day 41: 5% up, 4M volume → should trigger
        closes = [100.0] * 40 + [105.0] + [105.0] * 9
        volumes = [1_000_000] * 40 + [4_000_000] + [1_000_000] * 9
        _seed_ohlcv(store, "AAA", closes, volumes, start=date(2024, 1, 1))

        sig = VolumeSurgeSignal(lookback_days=20, abnormal_vol_threshold=2.0)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 4, 1)),
            date(2024, 2, 1),
            date(2024, 3, 22),
            ["AAA"],
        )
        non_null = panel["AAA"].dropna()
        # Expect exactly one event (the spike day)
        assert len(non_null) == 1, f"expected 1 event, got {len(non_null)}"
        assert non_null.iloc[0] > 0  # score is positive (log(vol/avg) * positive_return)

    def test_volume_spike_with_negative_return_filtered(self, tmp_path: Path) -> None:
        """Volume surge with NEGATIVE return is rejected (forward-momentum intent)."""
        store = ParquetStore(tmp_path)
        closes = [100.0] * 40 + [95.0] + [95.0] * 9
        volumes = [1_000_000] * 40 + [4_000_000] + [1_000_000] * 9
        _seed_ohlcv(store, "AAA", closes, volumes, start=date(2024, 1, 1))

        sig = VolumeSurgeSignal(lookback_days=20, abnormal_vol_threshold=2.0)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2024, 4, 1)),
            date(2024, 2, 1),
            date(2024, 3, 22),
            ["AAA"],
        )
        assert panel["AAA"].isna().all()

    def test_fingerprint_includes_params(self) -> None:
        a = VolumeSurgeSignal(lookback_days=20, abnormal_vol_threshold=2.0)
        b = VolumeSurgeSignal(lookback_days=20, abnormal_vol_threshold=3.0)
        assert a.fingerprint() != b.fingerprint()
