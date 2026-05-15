"""Unit tests for CrossSectionalMomentumSignal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.signals.technical.momentum import CrossSectionalMomentumSignal


def _seed_prices_linear(store: ParquetStore, ticker: str, start: date, days: int) -> None:
    """Seed a ticker with linear close prices 100, 101, 102, ... starting at `start` (weekdays)."""
    import pandas as pd

    idx = pd.date_range(start=start, periods=days, freq="B").date.tolist()
    closes = [100.0 + i for i in range(days)]
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


class TestConstruction:
    def test_default_params(self) -> None:
        sig = CrossSectionalMomentumSignal()
        assert sig.signal_id == "cross_sectional_momentum"
        assert sig.required_sources == ("yfinance.prices.daily",)

    def test_lookback_must_exceed_skip(self) -> None:
        with pytest.raises(ValueError, match="lookback_days"):
            CrossSectionalMomentumSignal(lookback_days=10, skip_days=10)

    def test_skip_days_non_negative(self) -> None:
        with pytest.raises(ValueError, match="skip_days"):
            CrossSectionalMomentumSignal(lookback_days=100, skip_days=-1)


class TestCompute:
    def test_empty_universe_returns_empty_panel(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        # Need at least one ticker on disk so scan doesn't FileNotFoundError —
        # but with empty universe param the signal short-circuits.
        _seed_prices_linear(store, "AAA", date(2020, 1, 1), days=300)
        sig = CrossSectionalMomentumSignal()
        panel = sig.compute(
            PITStoreView(store, as_of=date(2021, 1, 1)),
            date(2020, 12, 1),
            date(2020, 12, 31),
            [],
        )
        assert panel.shape[1] == 0

    def test_linear_prices_produce_finite_scores(self, tmp_path: Path) -> None:
        """A steadily increasing price series yields a positive 12-1 return."""
        store = ParquetStore(tmp_path)
        _seed_prices_linear(store, "AAA", date(2020, 1, 1), days=400)
        sig = CrossSectionalMomentumSignal(lookback_days=200, skip_days=20)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2021, 6, 1)),
            date(2021, 1, 1),
            date(2021, 3, 1),
            ["AAA"],
        )
        # All scores should be finite and positive (close is monotonically increasing)
        non_null = panel["AAA"].dropna()
        assert len(non_null) > 0, "expected some non-null scores in a fully-covered window"
        assert (non_null > 0).all()

    def test_insufficient_history_yields_nan(self, tmp_path: Path) -> None:
        """Window earlier than the lookback returns NaN."""
        store = ParquetStore(tmp_path)
        _seed_prices_linear(store, "AAA", date(2020, 1, 1), days=400)
        sig = CrossSectionalMomentumSignal(lookback_days=200, skip_days=20)
        # Request scores for the FIRST month of data — no 200-day history available.
        panel = sig.compute(
            PITStoreView(store, as_of=date(2020, 6, 1)),
            date(2020, 1, 2),
            date(2020, 1, 31),
            ["AAA"],
        )
        # Expect all NaN
        assert panel["AAA"].isna().all()

    def test_fingerprint_includes_params(self) -> None:
        a = CrossSectionalMomentumSignal(lookback_days=252, skip_days=21)
        b = CrossSectionalMomentumSignal(lookback_days=100, skip_days=10)
        assert a.fingerprint() != b.fingerprint()
        assert (
            a.fingerprint()
            == CrossSectionalMomentumSignal(lookback_days=252, skip_days=21).fingerprint()
        )

    def test_per_ticker_cross_section(self, tmp_path: Path) -> None:
        """Two tickers with different drifts produce different momentum scores."""
        import pandas as pd

        store = ParquetStore(tmp_path)
        # Ticker A: linear up 100→500 over 400 days (strong momentum)
        idx = pd.date_range(start=date(2020, 1, 1), periods=400, freq="B").date.tolist()
        a_closes = [100.0 + i for i in range(400)]
        # Ticker B: linear flat
        b_closes = [100.0] * 400
        for ticker, closes in (("AAA", a_closes), ("BBB", b_closes)):
            frame = pl.LazyFrame(
                {
                    "ticker": [ticker] * 400,
                    "date": idx,
                    "open": closes,
                    "high": closes,
                    "low": closes,
                    "close": closes,
                    "adj_close": closes,
                    "volume": [1_000_000] * 400,
                }
            )
            store.write("yfinance.prices.daily", frame, partition_keys=("ticker",))

        sig = CrossSectionalMomentumSignal(lookback_days=200, skip_days=20)
        panel = sig.compute(
            PITStoreView(store, as_of=date(2021, 6, 1)),
            date(2021, 3, 1),
            date(2021, 4, 1),
            ["AAA", "BBB"],
        )
        a_score = float(panel["AAA"].dropna().iloc[-1])
        b_score = float(panel["BBB"].dropna().iloc[-1])
        assert a_score > b_score, "trending-up ticker should outscore flat ticker"
        assert np.isclose(b_score, 0.0, atol=1e-9)
