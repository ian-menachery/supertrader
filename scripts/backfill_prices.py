"""Backfill daily OHLCV prices for the universe via yfinance.

Default window: 2022-01-01 to 2024-03-31 (matches the Reddit-sentiment backtest
window in `configs/runs/rsm_v1_backtest.yaml`).

Usage::

    uv run python scripts/backfill_prices.py
    uv run python scripts/backfill_prices.py --start 2024-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from supertrader.data.sources.prices_yfinance import YFinanceSource
from supertrader.data.store import ParquetStore
from supertrader.data.universe import StaticUniverse

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE = REPO_ROOT / "configs" / "universe" / "snapshot_2026_05_14.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 3, 31))
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument(
        "--data-dir", type=Path, default=REPO_ROOT / "data",
        help="ParquetStore root (default: repo/data/)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("backfill_prices")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    store = ParquetStore(args.data_dir)

    universe_loader = StaticUniverse.from_csv(args.universe)
    tickers = universe_loader.tickers()
    log.info("universe size: %d tickers", len(tickers))

    source = YFinanceSource()
    log.info("ingesting prices %s..%s for %d tickers", args.start, args.end, len(tickers))
    rows = source.ingest(args.start, args.end, tickers, store)
    log.info("wrote %d rows", rows)

    df = store.scan(source.source_id).collect()
    print(f"\nTotal rows on disk: {df.height}")
    print(f"Tickers: {df['ticker'].n_unique()}")
    print(f"Date range: {df['date'].min()!s} -> {df['date'].max()!s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
