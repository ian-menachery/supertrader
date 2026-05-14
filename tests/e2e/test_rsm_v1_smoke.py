"""End-to-end smoke for the full backtest pipeline on a tiny synthetic fixture.

Seeds the store with 5 tickers x 30 days of synthetic prices + Reddit posts,
runs `run_backtest()` against a config, and asserts the pipeline completes
without exception and the holdout remains untouched.
"""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from supertrader.backtest.splits import HoldoutTouchedError
from supertrader.data.store import ParquetStore
from supertrader.pipelines.run_backtest import run_backtest

TICKERS: list[str] = ["AAPL", "TSLA", "NVDA", "GME", "F"]


def _seed_prices(store: ParquetStore, start: date, days: int) -> None:
    rng = np.random.default_rng(seed=42)
    rows: list[dict[str, object]] = []
    for ticker in TICKERS:
        base = 100.0 + rng.uniform(0, 50)
        for d in range(days):
            day = start + timedelta(days=d)
            # Skip weekends; not strictly NYSE-correct but close enough for a fixture
            if day.weekday() >= 5:
                continue
            base *= 1.0 + rng.normal(0, 0.01)
            rows.append(
                {
                    "ticker": ticker,
                    "date": day,
                    "open": base * 0.99,
                    "high": base * 1.01,
                    "low": base * 0.98,
                    "close": base,
                    "adj_close": base,
                    "volume": int(rng.uniform(1e6, 1e7)),
                }
            )
    df = pl.DataFrame(rows)
    store.write("yfinance.prices.daily", df.lazy(), partition_keys=("ticker",))


def _seed_reddit(store: ParquetStore, start: date, days: int) -> None:
    rng = np.random.default_rng(seed=43)
    posts: list[dict[str, object]] = []
    pid = 0
    for d in range(days):
        day = start + timedelta(days=d)
        ym = f"{day.year:04d}-{day.month:02d}"
        for ticker in TICKERS:
            # 2-3 posts per ticker per day
            for _ in range(int(rng.integers(2, 4))):
                pid += 1
                # Polarity correlates loosely with day index modulo to give some signal
                sentiment_word = "bullish" if (d + hash(ticker)) % 3 == 0 else "bearish"
                posts.append(
                    {
                        "id": f"p{pid}",
                        "subreddit": "wsb",
                        "year_month": ym,
                        "author": "alice",
                        "created_utc": datetime.combine(day, datetime.min.time(), tzinfo=UTC),
                        "title": f"${ticker} {sentiment_word} today",
                        "selftext": "",
                        "score": int(rng.integers(1, 100)),
                        "num_comments": 0,
                        "url": "",
                        "permalink": "",
                    }
                )
    df = pl.DataFrame(posts)
    store.write("arctic_shift.posts", df.lazy(), partition_keys=("subreddit", "year_month"))


@pytest.fixture
def fixture_run_config(tmp_path: Path) -> Path:
    """Write a smoke RunConfig YAML pointing at the temp data dir."""
    config = textwrap.dedent("""\
        run_id: smoke-e2e-test
        universe:
          type: static
        data_sources:
          - type: yfinance.prices.daily
          - type: arctic_shift.posts
        signals:
          - type: reddit_sentiment
            name: reddit_sentiment_v1
            params:
              scorer:
                type: vader
                params:
                  lexicon_path: configs/sentiment_lexicon.yaml
              aggregation: mean
              sources: [arctic_shift.posts]
        strategy:
          type: mean_reversion
          signals: [reddit_sentiment_v1]
          params:
            quantile: 0.4
            min_signal_observations: 3
            target_gross: 1.0
        backtest:
          start: 2024-01-08
          end: 2024-02-09
          train_end: 2024-01-26
          test_end: 2024-02-02
          costs:
            commission_bps: 0
            slippage_bps_base: 0
            slippage_impact_coeff_bps: 0
            borrow_bps_annual: 0
        execution:
          type: backtest
    """)
    path = tmp_path / "smoke.yaml"
    path.write_text(config, encoding="utf-8")
    return path


@pytest.fixture
def fixture_universe_csv(tmp_path: Path) -> Path:
    body = "ticker,name,sector,market_cap_usd,adv_usd\n"
    for t in TICKERS:
        body += f"{t},{t} Inc.,Tech,1000000000000,1000000000\n"
    path = tmp_path / "universe.csv"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def fixture_blocklist(tmp_path: Path) -> Path:
    path = tmp_path / "blocklist.yaml"
    path.write_text("tickers: []\n", encoding="utf-8")
    return path


class TestPipelineSmoke:
    def test_pipeline_completes_without_holdout(
        self,
        tmp_path: Path,
        fixture_run_config: Path,
        fixture_universe_csv: Path,
        fixture_blocklist: Path,
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        store = ParquetStore(data_dir)
        _seed_prices(store, date(2024, 1, 1), days=45)
        _seed_reddit(store, date(2024, 1, 1), days=45)

        out = run_backtest(
            fixture_run_config,
            include_holdout=False,
            data_dir=data_dir,
            universe_path=fixture_universe_csv,
            blocklist_path=fixture_blocklist,
            allow_dirty=True,
        )

        assert out.config.run_id == "smoke-e2e-test"
        assert out.holdout_result is None  # untouched
        assert out.train_result is not None
        assert out.test_result is not None
        # Metrics JSON exists
        assert out.metrics_path.exists()
        payload = json.loads(out.metrics_path.read_text())
        assert payload["run_id"] == "smoke-e2e-test"
        assert payload["holdout"] is None
        # Manifest + tear sheet land on disk alongside metrics.
        assert (out.metrics_path.parent / "manifest.json").exists()
        assert out.tear_sheet_path.exists()
        assert out.manifest.status == "ok"
        # git_dirty is whatever the actual tree state is; we don't assert it
        # here because the developer running this test may have a clean tree.
        # SQLite manifest row matches the on-disk JSON.
        sqlite_row = ParquetStore(data_dir).get_run_manifest("smoke-e2e-test")
        assert sqlite_row is not None
        assert sqlite_row[7] == "ok"  # status column

    def test_holdout_guard_blocks_second_touch(
        self,
        tmp_path: Path,
        fixture_run_config: Path,
        fixture_universe_csv: Path,
        fixture_blocklist: Path,
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        store = ParquetStore(data_dir)
        _seed_prices(store, date(2024, 1, 1), days=45)
        _seed_reddit(store, date(2024, 1, 1), days=45)

        # First holdout touch — succeeds and records the config_hash.
        out1 = run_backtest(
            fixture_run_config,
            include_holdout=True,
            data_dir=data_dir,
            universe_path=fixture_universe_csv,
            blocklist_path=fixture_blocklist,
            allow_dirty=True,
        )
        assert out1.holdout_result is not None

        # Second touch with the same config — must raise.
        with pytest.raises(HoldoutTouchedError, match="already evaluated"):
            run_backtest(
                fixture_run_config,
                include_holdout=True,
                data_dir=data_dir,
                universe_path=fixture_universe_csv,
                blocklist_path=fixture_blocklist,
                allow_dirty=True,
            )
