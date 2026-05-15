"""PolygonEarningsSource — earnings dates + consensus + actuals from Polygon.

Stub. Phase B implements `fetch` against Polygon's vX/reference/financials
endpoint (or the dedicated earnings calendar if available at Stocks
Starter tier). Powers the PEAD signal (SUE = standardized unexpected
earnings) per Phase E in the pivot plan.

`sue` is intentionally NOT computed here. The source delivers raw
actual/estimate values; the SUE signal computes the standardized
unexpected value as a deliberate downstream step so the source stays
"raw data" and the signal stays "interpretation."
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
        "announcement_date": pl.Date,
        "fiscal_period": pl.Utf8,  # "Q1" | "Q2" | "Q3" | "Q4" | "FY"
        "fiscal_year": pl.Int64,
        "eps_estimate": pl.Float64,
        "eps_actual": pl.Float64,
        "eps_surprise": pl.Float64,  # actual - estimate (raw, not standardized)
        "announcement_time": pl.Utf8,  # "BMO" | "AMC" | "DMT" | null
    }
)


class PolygonEarningsSource:
    """Earnings dates + EPS estimate/actual from Polygon. Stub.

    Real implementation (Phase B) should:
      * Read POLYGON_API_KEY from the environment.
      * Pull the v2/reference/dividends-or-financials endpoint, or the
        earnings calendar if available at the Starter tier.
      * Handle re-statements: Polygon may revise historical actuals
        when a company files an amended 10-Q. The latest-known value
        wins; record the as_of timestamp on the partition.
      * Partition by `fiscal_year` for efficient backfill and reads.
    """

    source_id: str = "polygon.earnings"
    output_schema: pl.Schema = OUTPUT_SCHEMA

    def fetch(self, start: date, end: date, universe: list[str]) -> pl.LazyFrame:
        msg = (
            "PolygonEarningsSource.fetch is not yet implemented. "
            "Subscribe to Polygon Stocks Starter; the earnings calendar "
            "endpoint provides eps_estimate and eps_actual per "
            "(ticker, fiscal_period). See ADR 0008 + pivot plan Phase E."
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
            partition_keys=("fiscal_year",),
        )
