"""YFinanceSource integration tests with a mocked yfinance download.

We do not hit the network in CI. Real-network smoke validation is done manually
via `scripts/smoke_yfinance.py` (Phase 1: not yet written).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from supertrader.data.sources.prices_yfinance import (
    OUTPUT_SCHEMA,
    YFinanceSource,
)
from supertrader.data.store import ParquetStore


def _fake_single_ticker_response(ticker: str) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    return pd.DataFrame(
        {
            "Open": [180.0, 181.0, 182.0],
            "High": [185.0, 186.0, 187.0],
            "Low": [179.0, 180.5, 181.5],
            "Close": [184.0, 185.5, 186.5],
            "Adj Close": [183.8, 185.3, 186.3],
            "Volume": [1_000_000, 1_100_000, 1_050_000],
        },
        index=pd.Index(dates, name="Date"),
    )


def _fake_multi_ticker_response(tickers: list[str]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    arrays = []
    columns = []
    rng = np.random.default_rng(seed=42)
    for t in tickers:
        for f in fields:
            base = 100.0 if f != "Volume" else 1_000_000.0
            arrays.append(base + rng.normal(0, 5, size=len(dates)))
            columns.append((t, f))
    data = np.array(arrays).T
    return pd.DataFrame(
        data,
        index=pd.Index(dates, name="Date"),
        columns=pd.MultiIndex.from_tuples(columns, names=["Ticker", "Field"]),
    )


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


class TestYFinanceSourceFetch:
    def test_fetch_single_ticker(self) -> None:
        def fake_download(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            return _fake_single_ticker_response(tickers[0])

        source = YFinanceSource(download_fn=fake_download)
        df = source.fetch(date(2024, 1, 1), date(2024, 1, 5), ["AAPL"]).collect()
        assert df.schema == OUTPUT_SCHEMA
        assert df.height == 3
        assert (df["ticker"] == "AAPL").all()

    def test_fetch_multi_ticker(self) -> None:
        def fake_download(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            return _fake_multi_ticker_response(tickers)

        source = YFinanceSource(download_fn=fake_download)
        df = source.fetch(date(2024, 1, 1), date(2024, 1, 5), ["AAPL", "MSFT"]).collect()
        assert df.schema == OUTPUT_SCHEMA
        assert df.height == 6
        assert sorted(df["ticker"].unique().to_list()) == ["AAPL", "MSFT"]

    def test_empty_universe_returns_empty_frame(self) -> None:
        def fake_download(*args: Any, **kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        source = YFinanceSource(download_fn=fake_download)
        df = source.fetch(date(2024, 1, 1), date(2024, 1, 5), []).collect()
        assert df.height == 0
        assert df.schema == OUTPUT_SCHEMA

    def test_empty_yf_response_returns_empty_frame(self) -> None:
        def fake_download(*args: Any, **kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        source = YFinanceSource(download_fn=fake_download)
        df = source.fetch(date(2024, 1, 1), date(2024, 1, 5), ["AAPL"]).collect()
        assert df.height == 0


class TestYFinanceSourceIngest:
    def test_ingest_end_to_end(self, store: ParquetStore) -> None:
        def fake_download(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            return _fake_multi_ticker_response(tickers)

        source = YFinanceSource(download_fn=fake_download)
        rows = source.ingest(date(2024, 1, 1), date(2024, 1, 5), ["AAPL", "MSFT"], store)
        assert rows == 6
        df = store.scan(source.source_id).collect().sort(["ticker", "date"])
        assert df.height == 6
        assert "open" in df.columns
        assert "adj_close" in df.columns
        # ticker comes back as the partition column
        assert sorted(df["ticker"].unique().to_list()) == ["AAPL", "MSFT"]

    def test_reingest_idempotent_on_identical_data(self, store: ParquetStore) -> None:
        def fake_download(tickers: list[str], start: date, end: date) -> pd.DataFrame:
            return _fake_multi_ticker_response(tickers)

        source = YFinanceSource(download_fn=fake_download)
        source.ingest(date(2024, 1, 1), date(2024, 1, 5), ["AAPL", "MSFT"], store)
        rec1 = store.get_ingest_record(source.source_id, "ticker=AAPL")
        assert rec1 is not None

        source.ingest(date(2024, 1, 1), date(2024, 1, 5), ["AAPL", "MSFT"], store)
        rec2 = store.get_ingest_record(source.source_id, "ticker=AAPL")
        assert rec2 is not None
        assert rec1[1] == rec2[1]
