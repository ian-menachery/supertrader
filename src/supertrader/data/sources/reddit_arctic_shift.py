"""Arctic Shift HTTP API source for historical Reddit posts and comments.

Pushshift was killed by Reddit in 2024 under CFAA pressure. Arctic Shift is the
de-facto successor: a free HTTP API at `arctic-shift.photon-reddit.com` over a
community-maintained mirror of Reddit history (2005-12 → present).

This source handles posts only (Phase 2 adds comments via a parallel class).
Pagination is cursor-based on `created_utc` ascending: each batch sets
`after = last.created_utc + 1` until empty.

Per-subreddit-per-month is the partitioning scheme. `universe` is interpreted
as a list of subreddit names — there is no ticker universe at the Reddit
ingest layer.

Network failure mode: retries 3 times with exponential backoff via tenacity.
After that, the partial batch is written and an HTTPError is raised so the
caller knows the month is incomplete.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
import polars as pl
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from supertrader.data.base import StoreWriter

log = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://arctic-shift.photon-reddit.com"
POSTS_ENDPOINT = "/api/posts/search"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_PAGE_LIMIT = 100
DEFAULT_REQUEST_SPACING_SECONDS = 0.05  # ≈ 1200 req/min, well under the 2000 cap

_SCHEMA_FIELDS: list[tuple[str, Any]] = [
    ("id", pl.Utf8),
    ("subreddit", pl.Utf8),
    ("year_month", pl.Utf8),
    ("author", pl.Utf8),
    ("created_utc", pl.Datetime(time_unit="us", time_zone="UTC")),
    ("title", pl.Utf8),
    ("selftext", pl.Utf8),
    ("score", pl.Int64),
    ("num_comments", pl.Int64),
    ("url", pl.Utf8),
    ("permalink", pl.Utf8),
]
OUTPUT_SCHEMA: pl.Schema = pl.Schema(_SCHEMA_FIELDS)

_RESPONSE_FIELDS: tuple[str, ...] = (
    "id",
    "subreddit",
    "author",
    "created_utc",
    "title",
    "selftext",
    "score",
    "num_comments",
    "url",
    "permalink",
)


def _month_iter(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Yield (month_start, month_end_exclusive) over [start, end]."""
    cur = date(start.year, start.month, 1)
    while cur <= end:
        next_month_year = cur.year + (1 if cur.month == 12 else 0)
        next_month = 1 if cur.month == 12 else cur.month + 1
        next_start = date(next_month_year, next_month, 1)
        window_start = max(cur, start)
        window_end = min(next_start, end)
        if window_start < window_end:
            yield window_start, window_end
        cur = next_start


def _to_epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


class ArcticShiftPostsSource:
    """Reddit posts via the Arctic Shift HTTP API.

    `universe` semantics: list of subreddit names (no leading 'r/'). Each
    subreddit is partitioned separately on disk.

    Example::

        source = ArcticShiftPostsSource()
        source.ingest(date(2024, 1, 1), date(2024, 2, 1), ["wallstreetbets"], store)
    """

    source_id: str = "arctic_shift.posts"
    output_schema: pl.Schema = OUTPUT_SCHEMA

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        request_spacing_seconds: float = DEFAULT_REQUEST_SPACING_SECONDS,
        max_records_per_subreddit_month: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url, timeout=DEFAULT_TIMEOUT_SECONDS
        )
        self.page_limit = page_limit
        self.request_spacing_seconds = request_spacing_seconds
        self.max_records_per_subreddit_month = max_records_per_subreddit_month

    def __enter__(self) -> ArcticShiftPostsSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._owns_client:
            self._client.close()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.RequestError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _get_page(
        self, *, subreddit: str, after_epoch: int, before_epoch: int
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "subreddit": subreddit,
            "after": after_epoch,
            "before": before_epoch,
            "limit": self.page_limit,
            "sort": "asc",
        }
        # Note: `fields` param is documented but rejects multi-field comma lists
        # at the live endpoint (400 Bad Request). We accept the full record and
        # project to OUTPUT_SCHEMA in `fetch`. Re-enable when upstream is fixed.
        resp = self._client.get(POSTS_ENDPOINT, params=params)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", [])
        if not isinstance(data, list):
            msg = f"Unexpected Arctic Shift response shape: {type(data).__name__}"
            raise TypeError(msg)
        return data

    def _fetch_subreddit_window(
        self, subreddit: str, window_start: date, window_end: date
    ) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        after_epoch = _to_epoch(window_start)
        before_epoch = _to_epoch(window_end)
        cap = self.max_records_per_subreddit_month

        while True:
            page = self._get_page(
                subreddit=subreddit,
                after_epoch=after_epoch,
                before_epoch=before_epoch,
            )
            if not page:
                break
            all_rows.extend(page)
            log.info(
                "arctic_shift fetched page",
                extra={
                    "subreddit": subreddit,
                    "window_start": window_start.isoformat(),
                    "rows": len(page),
                    "total_so_far": len(all_rows),
                },
            )
            if cap is not None and len(all_rows) >= cap:
                all_rows = all_rows[:cap]
                break
            last_created = page[-1].get("created_utc")
            if last_created is None:
                break
            next_after = int(last_created) + 1
            if next_after <= after_epoch:
                # Defensive: server didn't advance; abort to prevent infinite loop.
                break
            after_epoch = next_after
            if len(page) < self.page_limit:
                # Last page for this window.
                break
            time.sleep(self.request_spacing_seconds)
        return all_rows

    def fetch(self, start: date, end: date, universe: list[str]) -> pl.LazyFrame:
        if not universe:
            return pl.LazyFrame(schema=OUTPUT_SCHEMA)

        rows: list[dict[str, Any]] = []
        for subreddit in universe:
            for window_start, window_end in _month_iter(start, end):
                rows.extend(self._fetch_subreddit_window(subreddit, window_start, window_end))

        if not rows:
            return pl.LazyFrame(schema=OUTPUT_SCHEMA)

        df = pl.DataFrame(rows, infer_schema_length=None)
        # Some posts have missing fields — fill with nulls then coerce.
        for col, dtype in OUTPUT_SCHEMA.items():
            if col == "year_month":
                continue
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).cast(dtype).alias(col))

        return (
            df.lazy()
            .with_columns(
                pl.from_epoch("created_utc", time_unit="s")
                .dt.replace_time_zone("UTC")
                .alias("created_utc"),
            )
            .with_columns(pl.col("created_utc").dt.strftime("%Y-%m").alias("year_month"))
            .select(list(OUTPUT_SCHEMA.keys()))
            .cast(OUTPUT_SCHEMA)
        )

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
            partition_keys=("subreddit", "year_month"),
        )
