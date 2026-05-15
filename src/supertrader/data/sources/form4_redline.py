"""Form4RedlineSource — Form 4 insider transactions via the redline boundary.

Stub. Phase B implements `fetch` against a Parquet export emitted by
`~/projects/redline` per ADR 0003's boundary contract. This source is
read-only from supertrader's perspective; redline owns the SEC EDGAR
ingest and 10b5-1 / option-exercise classification.

Two implementation options for Phase B:
  1. (Preferred) Add an `export_form4 --since DATE --to PATH` CLI in the
     redline project that writes Parquet matching `OUTPUT_SCHEMA`.
     supertrader reads the Parquet directly. Loose coupling; redline
     can change its internal schema freely.
  2. (Fallback) Read redline's sqlite DB directly via a read-only
     connection. Coupling on table names but no Parquet round-trip.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from supertrader.data.base import StoreWriter


OUTPUT_SCHEMA: pl.Schema = pl.Schema(
    {
        "issuer_ticker": pl.Utf8,
        "issuer_cik": pl.Utf8,
        "insider_cik": pl.Utf8,
        "insider_name": pl.Utf8,
        "insider_role": pl.Utf8,  # "CEO" | "CFO" | "Director" | "10% Owner" | ...
        "transaction_date": pl.Date,
        "filing_date": pl.Date,
        "transaction_code": pl.Utf8,  # SEC Table II codes: P, S, A, M, X, ...
        "is_10b5_1": pl.Boolean,  # True if the transaction was preplanned
        "shares": pl.Float64,
        "price_per_share": pl.Float64,
        "transaction_value_usd": pl.Float64,
    }
)


class Form4RedlineSource:
    """Form 4 transactions from redline's Parquet export. Stub.

    Real implementation (Phase B) should:
      * Accept a `redline_export_path` constructor arg pointing at the
        Parquet emitted by `redline export_form4`.
      * Partition output by year-month (transaction_date) for efficient
        rolling-window cluster computations downstream.
      * Filter to txn_code IN {"P"} for open-market buys when used by
        the Form 4 cluster signal — the SOURCE keeps all transactions,
        the SIGNAL filters.
    """

    source_id: str = "redline.form4"
    output_schema: pl.Schema = OUTPUT_SCHEMA

    def fetch(self, start: date, end: date, universe: list[str]) -> pl.LazyFrame:
        # `universe` here is a list of issuer tickers to filter to.
        msg = (
            "Form4RedlineSource.fetch is not yet implemented. "
            "Build the redline export CLI per ADR 0003 then implement "
            "this source against the Parquet output."
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
            partition_keys=("issuer_ticker", "filing_date"),
        )
