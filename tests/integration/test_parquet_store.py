"""ParquetStore integration tests: write, read, idempotency, metadata."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from supertrader.data.store import ParquetStore


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


def _sample_frame() -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT", "MSFT"],
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 3)],
            "close": [185.0, 186.5, 380.0, 382.0],
        }
    )


class TestParquetStoreWrite:
    def test_meta_db_initialized(self, store: ParquetStore) -> None:
        assert store.meta_db_path.exists()

    def test_write_creates_partitioned_files(self, store: ParquetStore) -> None:
        rows = store.write("test.prices", _sample_frame(), partition_keys=("ticker",))
        assert rows == 4
        aapl = store.root / "store" / "test" / "prices" / "ticker=AAPL" / "data.parquet"
        msft = store.root / "store" / "test" / "prices" / "ticker=MSFT" / "data.parquet"
        assert aapl.exists()
        assert msft.exists()

    def test_write_records_ingest_metadata(self, store: ParquetStore) -> None:
        store.write("test.prices", _sample_frame(), partition_keys=("ticker",))
        rec = store.get_ingest_record("test.prices", "ticker=AAPL")
        assert rec is not None
        rows, content_hash, _ts = rec
        assert rows == 2
        assert len(content_hash) == 32  # blake2b 16 bytes -> 32 hex chars

    def test_empty_frame_writes_nothing(self, store: ParquetStore) -> None:
        empty = pl.LazyFrame(schema={"ticker": pl.Utf8, "date": pl.Date, "close": pl.Float64})
        rows = store.write("test.prices", empty, partition_keys=("ticker",))
        assert rows == 0

    def test_missing_partition_key_raises(self, store: ParquetStore) -> None:
        frame = pl.LazyFrame({"ticker": ["AAPL"], "close": [100.0]})
        with pytest.raises(ValueError, match="Partition key 'date' missing"):
            store.write("test.prices", frame, partition_keys=("ticker", "date"))


class TestParquetStoreIdempotency:
    def test_reingest_overwrites_same_partition(self, store: ParquetStore) -> None:
        store.write("test.prices", _sample_frame(), partition_keys=("ticker",))
        rec1 = store.get_ingest_record("test.prices", "ticker=AAPL")
        assert rec1 is not None
        hash1 = rec1[1]

        # Reingest with different data for same partition
        updated = pl.LazyFrame(
            {
                "ticker": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [999.0, 999.5],  # different values
            }
        )
        store.write("test.prices", updated, partition_keys=("ticker",))

        rec2 = store.get_ingest_record("test.prices", "ticker=AAPL")
        assert rec2 is not None
        assert rec2[1] != hash1  # content changed

    def test_reingest_same_data_same_hash(self, store: ParquetStore) -> None:
        store.write("test.prices", _sample_frame(), partition_keys=("ticker",))
        rec1 = store.get_ingest_record("test.prices", "ticker=AAPL")
        assert rec1 is not None
        store.write("test.prices", _sample_frame(), partition_keys=("ticker",))
        rec2 = store.get_ingest_record("test.prices", "ticker=AAPL")
        assert rec2 is not None
        assert rec1[1] == rec2[1]  # bit-identical inputs => identical hash


class TestParquetStoreRead:
    def test_scan_roundtrip(self, store: ParquetStore) -> None:
        store.write("test.prices", _sample_frame(), partition_keys=("ticker",))
        df = store.scan("test.prices").collect().sort(["ticker", "date"])
        assert df.height == 4
        # Partition column comes back via hive_partitioning
        assert "ticker" in df.columns
        assert sorted(df["ticker"].unique().to_list()) == ["AAPL", "MSFT"]

    def test_scan_predicate_pushdown(self, store: ParquetStore) -> None:
        store.write("test.prices", _sample_frame(), partition_keys=("ticker",))
        df = store.scan("test.prices").filter(pl.col("ticker") == "AAPL").collect()
        assert df.height == 2
        assert (df["ticker"] == "AAPL").all()

    def test_scan_missing_source_raises(self, store: ParquetStore) -> None:
        with pytest.raises(FileNotFoundError):
            store.scan("nonexistent.source")
