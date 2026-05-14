"""ArcticShiftPostsSource integration tests with a mocked HTTP transport.

Real-network smoke runs via `scripts/smoke_arctic_shift.py` (gated on
`RUN_NETWORK_TESTS=1`).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from supertrader.data.sources.reddit_arctic_shift import (
    OUTPUT_SCHEMA,
    POSTS_ENDPOINT,
    ArcticShiftPostsSource,
    _month_iter,
)
from supertrader.data.store import ParquetStore


def _post(
    pid: str, subreddit: str, created_utc: int, title: str = "t", score: int = 1
) -> dict[str, object]:
    return {
        "id": pid,
        "subreddit": subreddit,
        "author": "alice",
        "created_utc": created_utc,
        "title": title,
        "selftext": "body",
        "score": score,
        "num_comments": 0,
        "url": "https://reddit.com/x",
        "permalink": f"/r/{subreddit}/comments/{pid}/",
    }


def _make_client(pages: list[list[dict[str, object]]]) -> httpx.Client:
    """Build an httpx.Client whose MockTransport returns the supplied page sequence."""
    call_count = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == POSTS_ENDPOINT
        idx = call_count["i"]
        call_count["i"] += 1
        if idx >= len(pages):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": pages[idx]})

    return httpx.Client(
        base_url="https://arctic-shift.photon-reddit.com",
        transport=httpx.MockTransport(handler),
    )


class TestMonthIter:
    def test_single_month_within_one_calendar_month(self) -> None:
        windows = list(_month_iter(date(2024, 1, 5), date(2024, 1, 20)))
        assert windows == [(date(2024, 1, 5), date(2024, 1, 20))]

    def test_spans_two_months(self) -> None:
        windows = list(_month_iter(date(2024, 1, 25), date(2024, 2, 10)))
        assert windows == [
            (date(2024, 1, 25), date(2024, 2, 1)),
            (date(2024, 2, 1), date(2024, 2, 10)),
        ]

    def test_full_year(self) -> None:
        windows = list(_month_iter(date(2024, 1, 1), date(2025, 1, 1)))
        assert len(windows) == 12
        assert windows[0] == (date(2024, 1, 1), date(2024, 2, 1))
        assert windows[-1] == (date(2024, 12, 1), date(2025, 1, 1))


class TestFetchPagination:
    def test_single_page_termination(self) -> None:
        pages = [[_post(f"p{i}", "wsb", 1_700_000_000 + i) for i in range(5)]]
        client = _make_client(pages)
        source = ArcticShiftPostsSource(client=client, page_limit=100, request_spacing_seconds=0)
        df = source.fetch(date(2023, 11, 14), date(2023, 11, 15), ["wsb"]).collect()
        assert df.height == 5
        assert df.schema == OUTPUT_SCHEMA
        assert df["subreddit"].unique().to_list() == ["wsb"]
        assert df["year_month"].unique().to_list() == ["2023-11"]

    def test_pagination_advances_cursor(self) -> None:
        pages = [
            [_post(f"p{i}", "wsb", 1_700_000_000 + i) for i in range(100)],
            [_post(f"q{i}", "wsb", 1_700_001_000 + i) for i in range(50)],
        ]
        client = _make_client(pages)
        source = ArcticShiftPostsSource(client=client, page_limit=100, request_spacing_seconds=0)
        df = source.fetch(date(2023, 11, 14), date(2023, 11, 15), ["wsb"]).collect()
        assert df.height == 150

    def test_empty_response_returns_empty_frame(self) -> None:
        client = _make_client([])
        source = ArcticShiftPostsSource(client=client, page_limit=100, request_spacing_seconds=0)
        df = source.fetch(date(2023, 11, 14), date(2023, 11, 15), ["wsb"]).collect()
        assert df.height == 0
        assert df.schema == OUTPUT_SCHEMA

    def test_max_records_cap_respected(self) -> None:
        pages = [[_post(f"p{i}", "wsb", 1_700_000_000 + i) for i in range(100)]] * 5
        client = _make_client(pages)
        source = ArcticShiftPostsSource(
            client=client,
            page_limit=100,
            request_spacing_seconds=0,
            max_records_per_subreddit_month=150,
        )
        df = source.fetch(date(2023, 11, 14), date(2023, 11, 15), ["wsb"]).collect()
        assert df.height == 150

    def test_empty_universe_returns_empty_frame(self) -> None:
        client = _make_client([])
        source = ArcticShiftPostsSource(client=client, request_spacing_seconds=0)
        df = source.fetch(date(2023, 11, 14), date(2023, 11, 15), []).collect()
        assert df.height == 0


class TestIngestEndToEnd:
    def test_ingest_writes_partitioned_parquet(self, tmp_path: Path) -> None:
        pages = [[_post(f"p{i}", "wsb", 1_700_000_000 + i) for i in range(7)]]
        client = _make_client(pages)
        source = ArcticShiftPostsSource(client=client, page_limit=100, request_spacing_seconds=0)
        store = ParquetStore(tmp_path)

        rows = source.ingest(date(2023, 11, 14), date(2023, 11, 15), ["wsb"], store)
        assert rows == 7

        df = store.scan(source.source_id).collect().sort("created_utc")
        assert df.height == 7
        # Partition columns come back from hive parsing
        assert "subreddit" in df.columns
        assert "year_month" in df.columns
        assert df["subreddit"].unique().to_list() == ["wsb"]

    def test_ingest_two_subreddits_two_partitions(self, tmp_path: Path) -> None:
        pages = [
            [_post(f"a{i}", "wsb", 1_700_000_000 + i) for i in range(3)],
            [_post(f"b{i}", "stocks", 1_700_000_000 + i) for i in range(2)],
        ]
        client = _make_client(pages)
        source = ArcticShiftPostsSource(client=client, page_limit=100, request_spacing_seconds=0)
        store = ParquetStore(tmp_path)
        rows = source.ingest(date(2023, 11, 14), date(2023, 11, 15), ["wsb", "stocks"], store)
        assert rows == 5

        wsb_dir = store.root / "store" / "arctic_shift" / "posts" / "subreddit=wsb"
        stocks_dir = store.root / "store" / "arctic_shift" / "posts" / "subreddit=stocks"
        assert any(wsb_dir.glob("**/data.parquet"))
        assert any(stocks_dir.glob("**/data.parquet"))


class TestRetryOnError:
    def test_http_error_propagates_after_retries(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "down"})

        client = httpx.Client(
            base_url="https://arctic-shift.photon-reddit.com",
            transport=httpx.MockTransport(handler),
        )
        source = ArcticShiftPostsSource(client=client, request_spacing_seconds=0)
        with pytest.raises(httpx.HTTPStatusError):
            source.fetch(date(2023, 11, 14), date(2023, 11, 15), ["wsb"]).collect()


class TestSchemaCompletion:
    def test_missing_fields_filled_with_nulls(self) -> None:
        # Simulate Arctic Shift returning posts where 'selftext' is missing
        partial = [{"id": "p1", "subreddit": "wsb", "created_utc": 1_700_000_000, "title": "x"}]
        client = _make_client([partial])
        source = ArcticShiftPostsSource(client=client, request_spacing_seconds=0)
        df = source.fetch(date(2023, 11, 14), date(2023, 11, 15), ["wsb"]).collect()
        assert df.height == 1
        assert df.schema == OUTPUT_SCHEMA
        # Missing fields became nulls
        assert df["selftext"][0] is None
        assert df["author"][0] is None
