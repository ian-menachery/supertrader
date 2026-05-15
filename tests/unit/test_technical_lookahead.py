"""Regression guard: technical signals must never reach beyond their PIT cutoff.

The PIT store filters by `timestamp <= as_of`, so any signal that scans
the prices source receives only "past" data by construction. This test
pins the contract: each technical signal's `required_sources` is just
the prices source, and each signal's `compute` only triggers
`scan("yfinance.prices.daily")` calls (never anything else, never a
spuriously-added source).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.technical.ma_cross import MovingAverageCrossSignal
from supertrader.signals.technical.momentum import CrossSectionalMomentumSignal
from supertrader.signals.technical.percent_change import PercentChangeSignal
from supertrader.signals.technical.reversal import ZScoreReversalSignal
from supertrader.signals.technical.rsi import RsiSignal
from supertrader.signals.technical.volume_surge import VolumeSurgeSignal

PRICES_SOURCE: str = "yfinance.prices.daily"
FORBIDDEN_SOURCES: list[str] = [
    "arctic_shift.posts",
    "redline.form4",
    "polygon.earnings",
    "eodhd.universe",
]


class _RecordingPITStore:
    """Wraps a real PITStoreView and records every `scan` call's source_id."""

    def __init__(self, inner: PITStoreView) -> None:
        self._inner = inner
        self.as_of: date = inner.as_of
        self.scanned: list[str] = []

    def scan(self, source_id: str) -> pl.LazyFrame:
        self.scanned.append(source_id)
        return self._inner.scan(source_id)


def _seed_prices(store: ParquetStore, ticker: str, days: int) -> None:
    idx = pd.date_range(start=date(2024, 1, 1), periods=days, freq="B").date.tolist()
    closes = [100.0 + i * 0.5 for i in range(days)]
    frame = pl.LazyFrame(
        {
            "ticker": [ticker] * days,
            "date": idx,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": closes,
            "volume": [1_000_000] * days,
        }
    )
    store.write("yfinance.prices.daily", frame, partition_keys=("ticker",))


@pytest.fixture
def seeded_store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(tmp_path)
    _seed_prices(store, "AAA", days=300)
    return store


@pytest.mark.parametrize(
    "signal_factory",
    [
        lambda: CrossSectionalMomentumSignal(lookback_days=200, skip_days=20),
        lambda: ZScoreReversalSignal(lookback_days=20),
        lambda: VolumeSurgeSignal(lookback_days=20),
        lambda: PercentChangeSignal(lookback_days=1),
        lambda: MovingAverageCrossSignal(fast_window=10, slow_window=30),
        lambda: RsiSignal(window=14),
    ],
    ids=["momentum", "reversal", "volume_surge", "percent_change", "ma_cross", "rsi"],
)
def test_required_sources_is_only_prices(signal_factory: object) -> None:
    """Static contract: every technical signal declares prices and nothing else."""
    sig = signal_factory()  # type: ignore[operator]
    assert sig.required_sources == (PRICES_SOURCE,), (
        f"signal {type(sig).__name__} declared {sig.required_sources}; "
        f"expected just {PRICES_SOURCE}"
    )


@pytest.mark.parametrize(
    "signal_factory",
    [
        lambda: CrossSectionalMomentumSignal(lookback_days=200, skip_days=20),
        lambda: ZScoreReversalSignal(lookback_days=20),
        lambda: VolumeSurgeSignal(lookback_days=20),
        lambda: PercentChangeSignal(lookback_days=1),
        lambda: MovingAverageCrossSignal(fast_window=10, slow_window=30),
        lambda: RsiSignal(window=14),
    ],
    ids=["momentum", "reversal", "volume_surge", "percent_change", "ma_cross", "rsi"],
)
def test_compute_never_scans_forbidden_sources(
    seeded_store: ParquetStore, signal_factory: object
) -> None:
    """Runtime check: signal.compute() asks the store only for the prices source."""
    pit = PITStoreView(seeded_store, as_of=date(2025, 6, 1))
    recorder = _RecordingPITStore(pit)
    sig = signal_factory()  # type: ignore[operator]
    sig.compute(recorder, date(2024, 12, 1), date(2025, 5, 30), ["AAA"])  # type: ignore[attr-defined]

    # Sanity: at least one scan happened
    assert recorder.scanned, "expected compute() to issue at least one scan()"
    # Every scan call was for prices, never any forbidden source.
    for forbidden in FORBIDDEN_SOURCES:
        assert forbidden not in recorder.scanned, (
            f"signal {type(sig).__name__} unexpectedly scanned {forbidden!r}"
        )
    unexpected = set(recorder.scanned) - {PRICES_SOURCE}
    assert set(recorder.scanned) == {PRICES_SOURCE}, (
        f"signal {type(sig).__name__} scanned unexpected sources: {unexpected}"
    )
