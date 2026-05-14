"""Tests for `data.store.PITStoreView` — the no-lookahead-bias guard."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from supertrader.data.store import (
    TIMESTAMP_COLUMN_FOR_SOURCE,
    ParquetStore,
    PITStoreView,
)
from supertrader.signals.base import PointInTimeStore


@pytest.fixture
def store_with_yfinance(tmp_path: Path) -> ParquetStore:
    s = ParquetStore(tmp_path)
    frame = pl.LazyFrame(
        {
            "ticker": ["AAPL"] * 5 + ["MSFT"] * 5,
            "date": [date(2024, 1, i) for i in (2, 3, 4, 5, 8)] * 2,
            "close": [180.0, 181.0, 182.0, 183.0, 184.0, 380.0, 381.0, 382.0, 383.0, 384.0],
        }
    )
    s.write("yfinance.prices.daily", frame, partition_keys=("ticker",))
    return s


@pytest.fixture
def store_with_arctic_shift(tmp_path: Path) -> ParquetStore:
    s = ParquetStore(tmp_path)
    # Datetime-typed timestamp column to exercise the cast path
    frame = pl.LazyFrame(
        {
            "id": [f"p{i}" for i in range(5)],
            "subreddit": ["wsb"] * 5,
            "year_month": ["2024-01"] * 5,
            "created_utc": [datetime(2024, 1, d, 10, 0, tzinfo=UTC) for d in (2, 3, 4, 5, 8)],
            "title": ["t"] * 5,
        }
    )
    s.write("arctic_shift.posts", frame, partition_keys=("subreddit", "year_month"))
    return s


class TestProtocolSatisfaction:
    def test_pit_view_satisfies_point_in_time_store_protocol(self, tmp_path: Path) -> None:
        s = ParquetStore(tmp_path)
        view = PITStoreView(s, as_of=date(2024, 1, 5))
        assert isinstance(view, PointInTimeStore)


class TestDateColumnFiltering:
    def test_filters_rows_past_as_of(self, store_with_yfinance: ParquetStore) -> None:
        view = PITStoreView(store_with_yfinance, as_of=date(2024, 1, 4))
        df = view.scan("yfinance.prices.daily").collect()
        # Jan 2, 3, 4 survive (date <= 2024-01-04) — 3 rows per ticker, 2 tickers = 6 rows
        assert df.height == 6
        assert df["date"].max() == date(2024, 1, 4)

    def test_includes_as_of_boundary(self, store_with_yfinance: ParquetStore) -> None:
        view = PITStoreView(store_with_yfinance, as_of=date(2024, 1, 8))
        df = view.scan("yfinance.prices.daily").collect()
        assert df.height == 10  # all rows
        assert df["date"].max() == date(2024, 1, 8)

    def test_as_of_before_all_data_returns_empty(self, store_with_yfinance: ParquetStore) -> None:
        view = PITStoreView(store_with_yfinance, as_of=date(2023, 12, 31))
        df = view.scan("yfinance.prices.daily").collect()
        assert df.height == 0


class TestDatetimeColumnFiltering:
    def test_datetime_column_cast_to_date_for_comparison(
        self, store_with_arctic_shift: ParquetStore
    ) -> None:
        view = PITStoreView(store_with_arctic_shift, as_of=date(2024, 1, 4))
        df = view.scan("arctic_shift.posts").collect()
        # Jan 2, 3, 4 — 3 rows
        assert df.height == 3


class TestUnknownSource:
    def test_unknown_source_raises_with_helpful_list(
        self, store_with_yfinance: ParquetStore
    ) -> None:
        view = PITStoreView(store_with_yfinance, as_of=date(2024, 1, 5))
        with pytest.raises(KeyError, match="no registered timestamp column"):
            view.scan("nonexistent.source")


class TestUniverseSnapshotRecording:
    def test_record_and_query(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        store.record_universe_snapshot(
            "russell1000_v1",
            date(2026, 5, 14),
            [
                ("AAPL", 3.5e12, "Technology"),
                ("F", 4.8e10, "Consumer Cyclical"),
                ("GME", 7e9, "Consumer Cyclical"),
            ],
        )
        with sqlite3.connect(store.meta_db_path) as conn:
            rows = conn.execute(
                "SELECT ticker, sector FROM universe_snapshots "
                "WHERE snapshot_id = 'russell1000_v1' ORDER BY ticker"
            ).fetchall()
        assert rows == [
            ("AAPL", "Technology"),
            ("F", "Consumer Cyclical"),
            ("GME", "Consumer Cyclical"),
        ]


class TestTimestampMapping:
    def test_known_sources_registered(self) -> None:
        # If you add a source, you must register its timestamp column here.
        assert "yfinance.prices.daily" in TIMESTAMP_COLUMN_FOR_SOURCE
        assert "arctic_shift.posts" in TIMESTAMP_COLUMN_FOR_SOURCE
