"""EODHDUniverseSource — PIT historical-constituents panels from EODHD.

Stub. Phase B implements `fetch` against the EODHD historical-tickers
endpoint. Powers `PITUniverse` per ADR 0012.

Output schema is a `(date, ticker, included)` long frame, one row per
ticker per day the ticker is a constituent. The downstream `PITUniverse`
class loads this into a panel keyed by `as_of` date.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from supertrader.data.base import StoreWriter


OUTPUT_SCHEMA: pl.Schema = pl.Schema(
    {
        "index": pl.Utf8,  # "sp500" | "russell1000" | "russell3000"
        "date": pl.Date,
        "ticker": pl.Utf8,
        "included": pl.Boolean,
    }
)

_VALID_INDEXES: frozenset[str] = frozenset({"sp500", "russell1000", "russell3000"})


class EODHDUniverseSource:
    """PIT index constituents from EODHD. Stub — `fetch` raises NotImplementedError.

    Real implementation (Phase B) should:
      * Read EODHD_API_KEY from the environment.
      * Pull `https://eodhistoricaldata.com/api/fundamentals/<INDEX>.INDX`
        which includes a `HistoricalComponents` section.
      * Densify the membership intervals into per-day rows.
      * Validate that `index` is in `_VALID_INDEXES`.
      * Partition output by `index` so a backtest can load just the
        slice it needs.
    """

    source_id: str = "eodhd.universe"
    output_schema: pl.Schema = OUTPUT_SCHEMA

    def fetch(self, start: date, end: date, universe: list[str]) -> pl.LazyFrame:
        # `universe` is interpreted here as a list of index names
        # ("sp500", etc.), NOT tickers — semantic difference from
        # YFinanceSource. Documented loudly.
        msg = (
            "EODHDUniverseSource.fetch is not yet implemented. "
            "Subscribe to EODHD and implement against the historical-"
            "components endpoint. See ADR 0008 + ADR 0012."
        )
        raise NotImplementedError(msg)

    def ingest(
        self,
        start: date,
        end: date,
        universe: list[str],
        store: StoreWriter,
    ) -> int:
        return store.write(
            self.source_id,
            self.fetch(start, end, universe),
            partition_keys=("index",),
        )
