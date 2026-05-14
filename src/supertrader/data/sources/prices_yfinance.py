"""yfinance DataSource for US equity daily OHLCV.

yfinance returns pandas with either single- or MultiIndex columns depending on
universe size. This source normalizes both into a long-form Polars frame with
the canonical schema:

    ticker (Utf8) | date (Date) | open (f64) | high (f64) | low (f64) |
    close (f64)   | adj_close (f64) | volume (i64)
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd
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

_FIELD_ALIASES: dict[str, str] = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def _reshape_single_ticker(df: pd.DataFrame, ticker: str) -> pl.DataFrame:
    """Single-ticker yfinance frame: flat columns Open, High, Low, Close, Adj Close, Volume."""
    if df.empty:
        return pl.DataFrame(schema=OUTPUT_SCHEMA)
    out = df.rename(columns=_FIELD_ALIASES).reset_index()
    out["ticker"] = ticker
    # yfinance index is "Date" (naive datetime). Convert to date.
    out = out.rename(columns={"Date": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.date
    cols = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]
    return pl.from_pandas(out[cols]).cast(OUTPUT_SCHEMA)


def _reshape_multi_ticker(df: pd.DataFrame) -> pl.DataFrame:
    """Multi-ticker yfinance frame with MultiIndex columns (ticker, field)."""
    if df.empty:
        return pl.DataFrame(schema=OUTPUT_SCHEMA)
    stacked = df.stack(level=0, future_stack=True).reset_index()
    stacked = stacked.rename(columns={**_FIELD_ALIASES, "Date": "date", "Ticker": "ticker"})
    if "level_1" in stacked.columns:
        stacked = stacked.rename(columns={"level_1": "ticker"})
    stacked["date"] = pd.to_datetime(stacked["date"]).dt.date
    cols = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]
    missing = [c for c in cols if c not in stacked.columns]
    if missing:
        msg = f"yfinance output missing expected columns after reshape: {missing}. Got: {list(stacked.columns)}"
        raise ValueError(msg)
    return pl.from_pandas(stacked[cols]).cast(OUTPUT_SCHEMA)


def _download(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Thin wrapper around yfinance.download — isolated so tests can monkeypatch it."""
    import yfinance as yf

    return yf.download(  # type: ignore[no-any-return]
        tickers=tickers,
        start=start.isoformat(),
        end=end.isoformat(),
        auto_adjust=False,
        progress=False,
        group_by="ticker" if len(tickers) > 1 else "column",
        threads=True,
    )


class YFinanceSource:
    """Daily OHLCV from Yahoo Finance via the `yfinance` package."""

    source_id: str = "yfinance.prices.daily"
    output_schema: pl.Schema = OUTPUT_SCHEMA

    def __init__(self, download_fn: Any | None = None) -> None:  # noqa: ANN401
        # Injection seam for tests; defaults to the real yfinance.download.
        self._download = download_fn or _download

    def fetch(self, start: date, end: date, universe: list[str]) -> pl.LazyFrame:
        if not universe:
            return pl.LazyFrame(schema=OUTPUT_SCHEMA)
        raw = self._download(universe, start, end)
        if not isinstance(raw, pd.DataFrame) or raw.empty:
            return pl.LazyFrame(schema=OUTPUT_SCHEMA)
        if len(universe) == 1:
            shaped = _reshape_single_ticker(raw, universe[0])
        else:
            shaped = _reshape_multi_ticker(raw)
        return shaped.lazy()

    def ingest(
        self,
        start: date,
        end: date,
        universe: list[str],
        store: StoreWriter,
    ) -> int:
        return store.write(
            self.source_id, self.fetch(start, end, universe), partition_keys=("ticker",)
        )
