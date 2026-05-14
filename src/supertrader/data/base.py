"""DataSource protocol and store-writer interface.

A `DataSource` ingests external data into the canonical Parquet store. The store
is the only authority on what data exists locally; sources never read from it.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import polars as pl


@runtime_checkable
class StoreWriter(Protocol):
    """Minimal write surface exposed to DataSources.

    The full store interface (readers, point-in-time views) lives in `data.store`.
    Sources only need to write, atomically and idempotently.
    """

    def write(
        self,
        source_id: str,
        frame: pl.LazyFrame,
        *,
        partition_keys: tuple[str, ...],
    ) -> int:
        """Write `frame` partitioned by `partition_keys`. Returns rows written.

        Implementations must be atomic (write-tmp-then-rename) and idempotent
        (re-running over the same partition keys overwrites that partition only).
        """
        ...


@runtime_checkable
class DataSource(Protocol):
    """A source of external data.

    Contract:
      * `source_id` is stable across versions — used as a cache key.
      * `output_schema` declares the columns this source produces. The store
        validates incoming frames against it on write.
      * `fetch` is pure: same inputs → same output `LazyFrame`. No side effects.
      * `ingest` = fetch + write. Idempotent over (start, end, universe).
      * Sources do not read from the store. They are write-only producers.
    """

    source_id: str
    output_schema: pl.Schema

    def fetch(
        self,
        start: date,
        end: date,
        universe: list[str],
    ) -> pl.LazyFrame:
        """Materialize data for the given window and universe as a LazyFrame."""
        ...

    def ingest(
        self,
        start: date,
        end: date,
        universe: list[str],
        store: StoreWriter,
    ) -> int:
        """Fetch + write to `store`. Returns number of rows written."""
        ...
