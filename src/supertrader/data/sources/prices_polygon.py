"""PolygonPricesSource — daily OHLCV from Polygon Stocks Starter.

Stub. Phase B in the trading-system pivot plan implements `fetch` against
the live API. The protocol is fixed now so downstream code (signals,
strategies, the cost model) can be drafted against a known shape.

See ADR 0008 for the subscription decision and ADR 0010 for how spread
data (a Polygon side-product) feeds the v2 cost model.

Output schema is intentionally identical to
`yfinance.prices.daily` so the pipeline's `_load_prices` doesn't care
which source wrote a partition. Switching providers is a config change,
not a code change.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from supertrader.data.base import StoreWriter


OUTPUT_SCHEMA: pl.Schema = pl.Schema(
    {
        "ticker": pl.Utf8,
        "date": pl.Date,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "adj_close": pl.Float64,
        "volume": pl.Int64,
    }
)


class PolygonPricesSource:
    """Daily OHLCV from Polygon. Stub — `fetch` raises NotImplementedError.

    Real implementation (Phase B) should:
      * Read POLYGON_API_KEY from the environment (not from config).
      * Use the official `polygon-api-client` if its dep cost is small;
        otherwise hand-rolled httpx with tenacity retries.
      * Stream per-ticker (or per-month) so a multi-thousand-ticker
        backfill doesn't OOM — mirror `reddit_arctic_shift.fetch_months`.
      * Honor Polygon Starter's rate limits.
      * Write per-ticker partitions identical in shape to the existing
        yfinance store so the rest of the pipeline is provider-agnostic.
    """

    source_id: str = "polygon.prices.daily"
    output_schema: pl.Schema = OUTPUT_SCHEMA

    def fetch(self, start: date, end: date, universe: list[str]) -> pl.LazyFrame:
        msg = (
            "PolygonPricesSource.fetch is not yet implemented. "
            "Subscribe to Polygon Stocks Starter and implement against "
            "the docs at https://polygon.io/docs/rest/stocks. See ADR 0008."
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
            partition_keys=("ticker",),
        )
