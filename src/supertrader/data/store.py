"""ParquetStore: the canonical on-disk store for time-series data.

One ParquetStore instance owns:
  * A root directory where partitioned Parquet datasets live.
  * A SQLite metadata DB (`meta.sqlite`) tracking what's been ingested.

The Parquet path layout for a source with id `yfinance.prices.daily` and a
partition by `ticker` looks like::

    {root}/store/yfinance/prices/daily/ticker=AAPL/data.parquet
    {root}/store/yfinance/prices/daily/ticker=MSFT/data.parquet
    ...

Writes are atomic per partition (`data.parquet.tmp` → rename). Re-ingesting the
same source for the same partition keys overwrites the partition only.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_ingests (
  source_id     TEXT NOT NULL,
  partition_key TEXT NOT NULL,
  rows          INTEGER NOT NULL,
  content_hash  TEXT NOT NULL,
  ingested_at   TEXT NOT NULL,
  PRIMARY KEY (source_id, partition_key)
);
CREATE INDEX IF NOT EXISTS idx_source_ingests_source ON source_ingests(source_id);
"""


def _hash_file(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_path_segment(source_id: str) -> str:
    """Convert `yfinance.prices.daily` → `yfinance/prices/daily`."""
    return source_id.replace(".", "/")


def _partition_dir_name(key: str, value: object) -> str:
    return f"{key}={value}"


class ParquetStore:
    """Filesystem-backed store. One instance per `root` directory.

    Implements the `StoreWriter` protocol via duck typing; also exposes `scan`
    for read access and `get_ingest_record` for metadata queries.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "store").mkdir(exist_ok=True)
        self.meta_db_path = self.root / "meta.sqlite"
        self._init_meta_db()

    def _init_meta_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.meta_db_path, isolation_level=None)
        try:
            yield conn
        finally:
            conn.close()

    def _source_root(self, source_id: str) -> Path:
        return self.root / "store" / _source_path_segment(source_id)

    def write(
        self,
        source_id: str,
        frame: pl.LazyFrame,
        *,
        partition_keys: tuple[str, ...],
    ) -> int:
        """Write a frame partitioned by `partition_keys`. Returns rows written.

        Idempotent: each partition's file is overwritten if it already exists.
        Atomic per-partition via tmp-then-rename.
        """
        df = frame.collect()
        if df.is_empty():
            return 0

        for key in partition_keys:
            if key not in df.columns:
                msg = (
                    f"Partition key '{key}' missing from frame for source '{source_id}'. "
                    f"Available columns: {df.columns}."
                )
                raise ValueError(msg)

        source_root = self._source_root(source_id)
        source_root.mkdir(parents=True, exist_ok=True)
        total_rows = 0

        if partition_keys:
            unique = df.select(list(partition_keys)).unique()
            for part_row in unique.iter_rows(named=True):
                expr: pl.Expr | None = None
                for k, v in part_row.items():
                    cond = pl.col(k) == v
                    expr = cond if expr is None else (expr & cond)
                assert expr is not None
                part_df = df.filter(expr).drop(list(partition_keys))

                part_dir = source_root
                for k, v in part_row.items():
                    part_dir = part_dir / _partition_dir_name(k, v)
                part_dir.mkdir(parents=True, exist_ok=True)

                target = part_dir / "data.parquet"
                tmp = part_dir / "data.parquet.tmp"
                part_df.write_parquet(tmp)
                target.unlink(missing_ok=True)
                tmp.rename(target)

                partition_key = "/".join(_partition_dir_name(k, v) for k, v in part_row.items())
                self._record_ingest(source_id, partition_key, part_df.height, _hash_file(target))
                total_rows += part_df.height
        else:
            target = source_root / "data.parquet"
            tmp = source_root / "data.parquet.tmp"
            df.write_parquet(tmp)
            target.unlink(missing_ok=True)
            tmp.rename(target)
            self._record_ingest(source_id, "_root", df.height, _hash_file(target))
            total_rows = df.height

        return total_rows

    def _record_ingest(
        self, source_id: str, partition_key: str, rows: int, content_hash: str
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO source_ingests "
                "(source_id, partition_key, rows, content_hash, ingested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, partition_key, rows, content_hash, now),
            )

    def scan(self, source_id: str) -> pl.LazyFrame:
        """Lazy scan of all partitions for a source. Hive-style partitioning auto-detected."""
        source_root = self._source_root(source_id)
        if not source_root.exists():
            msg = f"No data for source '{source_id}' at {source_root}."
            raise FileNotFoundError(msg)
        return pl.scan_parquet(
            str(source_root / "**" / "data.parquet"),
            hive_partitioning=True,
        )

    def get_ingest_record(
        self, source_id: str, partition_key: str
    ) -> tuple[int, str, str] | None:
        """Return (rows, content_hash, ingested_at) for a partition, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rows, content_hash, ingested_at FROM source_ingests "
                "WHERE source_id = ? AND partition_key = ?",
                (source_id, partition_key),
            ).fetchone()
        if row is None:
            return None
        return (int(row[0]), str(row[1]), str(row[2]))
