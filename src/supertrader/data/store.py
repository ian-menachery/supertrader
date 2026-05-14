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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterator


# Maps source_id -> column name that carries the as-of timestamp for PIT filtering.
# Adding a new source means registering it here too.
TIMESTAMP_COLUMN_FOR_SOURCE: dict[str, str] = {
    "yfinance.prices.daily": "date",
    "arctic_shift.posts": "created_utc",
}


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

-- Universe snapshots: which tickers belonged to a given universe at a given date.
CREATE TABLE IF NOT EXISTS universe_snapshots (
  snapshot_id    TEXT NOT NULL,
  as_of_date     TEXT NOT NULL,
  ticker         TEXT NOT NULL,
  market_cap_usd REAL,
  sector         TEXT,
  PRIMARY KEY (snapshot_id, as_of_date, ticker)
);

-- Run manifests: the reproducibility ledger. Populated by the backtest engine in Week 4.
CREATE TABLE IF NOT EXISTS run_manifests (
  run_id          TEXT PRIMARY KEY,
  config_path     TEXT NOT NULL,
  config_hash     TEXT NOT NULL,
  git_sha         TEXT NOT NULL,
  python_version  TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  ended_at        TEXT,
  status          TEXT NOT NULL,
  data_hashes     TEXT NOT NULL
);

-- Holdout-touch ledger. The UNIQUE constraint on config_hash is the forcing function:
-- one holdout evaluation per config_hash, ever (until scripts/reset_holdout_lock.py runs).
CREATE TABLE IF NOT EXISTS holdout_touches (
  run_id      TEXT NOT NULL,
  config_hash TEXT NOT NULL UNIQUE,
  touched_at  TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES run_manifests(run_id)
);

-- Manual patches over data-source-reported corporate actions.
CREATE TABLE IF NOT EXISTS corp_action_overrides (
  ticker      TEXT NOT NULL,
  ex_date     TEXT NOT NULL,
  action_type TEXT NOT NULL CHECK (action_type IN ('split', 'dividend')),
  ratio       REAL,
  amount      REAL,
  source_note TEXT,
  PRIMARY KEY (ticker, ex_date, action_type)
);

-- Signal cache: lookup table from (signal_id, fingerprint) to the parquet path.
CREATE TABLE IF NOT EXISTS signal_cache (
  signal_id    TEXT NOT NULL,
  fingerprint  TEXT NOT NULL,
  date_start   TEXT NOT NULL,
  date_end     TEXT NOT NULL,
  parquet_path TEXT NOT NULL,
  computed_at  TEXT NOT NULL,
  PRIMARY KEY (signal_id, fingerprint, date_start, date_end)
);
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

    def record_universe_snapshot(
        self,
        snapshot_id: str,
        as_of: date,
        entries: list[tuple[str, float | None, str | None]],
    ) -> None:
        """Insert `(ticker, market_cap_usd, sector)` rows into `universe_snapshots`.

        Idempotent via `INSERT OR REPLACE` on the composite PK.
        """
        as_of_iso = as_of.isoformat()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO universe_snapshots "
                "(snapshot_id, as_of_date, ticker, market_cap_usd, sector) "
                "VALUES (?, ?, ?, ?, ?)",
                [(snapshot_id, as_of_iso, t, mc, s) for (t, mc, s) in entries],
            )


class PITStoreView:
    """A point-in-time view over a `ParquetStore`.

    Every `scan(source_id)` returns a `LazyFrame` filtered to rows with
    the source's timestamp column ≤ `as_of`. The timestamp column is looked up
    in `TIMESTAMP_COLUMN_FOR_SOURCE`. For `Datetime` columns the comparison
    is cast to `Date` so date-level filtering works uniformly.

    Satisfies `supertrader.signals.base.PointInTimeStore` via duck typing.
    """

    def __init__(self, store: ParquetStore, as_of: date) -> None:
        self._store = store
        self.as_of = as_of

    def scan(self, source_id: str) -> pl.LazyFrame:
        ts_col = TIMESTAMP_COLUMN_FOR_SOURCE.get(source_id)
        if ts_col is None:
            available = ", ".join(sorted(TIMESTAMP_COLUMN_FOR_SOURCE.keys())) or "<empty>"
            msg = (
                f"Source '{source_id}' has no registered timestamp column. "
                f"Add it to TIMESTAMP_COLUMN_FOR_SOURCE. Available: {available}"
            )
            raise KeyError(msg)
        lazy = self._store.scan(source_id)
        # Cast to Date so we accept both Date and Datetime source columns.
        return lazy.filter(pl.col(ts_col).cast(pl.Date) <= pl.lit(self.as_of))
