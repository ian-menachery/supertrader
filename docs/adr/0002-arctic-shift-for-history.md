# ADR 0002 — Arctic Shift HTTP API for Reddit historical data

**Status**: Accepted
**Date**: 2026-05-14

## Context

Pushshift, the historical standard for Reddit data, was killed by Reddit in 2024
under CFAA pressure. Supertrader needs ~2020–present Reddit posts (and comments,
later) from a handful of finance-adjacent subreddits — wallstreetbets, stocks,
investing, options, SecurityAnalysis, StockMarket — to backfill a sentiment
signal.

Three options were considered for accessing this history:

1. **Arctic Shift Academic Torrents** — full monthly all-Reddit dumps, ~261 GB
   compressed across the archive. No per-subreddit dump exists.
2. **Arctic Shift HTTP API** — `https://arctic-shift.photon-reddit.com/api/*`,
   no auth, documented rate limit ~2000 req/min, max 100 results per request.
3. **Reddit live API via PRAW only** — forward-only ingest; no historical access.

## Decision

**Use the Arctic Shift HTTP API for historical backfill.** PRAW will be added
later for forward-only ingest (catches anything posted after the last Arctic
Shift mirror cycle).

## Validation evidence

The Week-1 smoke test (`scripts/smoke_arctic_shift.py`) pulled 500 r/wallstreetbets
posts spanning 2024-01-15 → 2024-01-16 in roughly 10 seconds:

- 5 paginated requests, each returning 100 posts at ~700–900ms latency
- 0% null on `id`, `subreddit`, `author`, `created_utc`, `title`, `selftext`,
  `score`, `num_comments`, `url`, `permalink`
- Cursor-on-`created_utc` pagination advances correctly

Extrapolated to a full month of WSB (~10–15K posts), ingest cost is
~100–150 requests, ~2 minutes wall time. A full six-year WSB backfill is on the
order of a few hours.

## Constraints discovered

- **`fields` query parameter does not work** on the live endpoint as documented.
  It returns HTTP 400 when any comma-separated list is supplied. We accept the
  full record and project to `OUTPUT_SCHEMA` in `fetch()`. Re-enable when
  upstream is fixed.
- No SLA; the project is community-run with "no uptime or performance guarantees."
  This is the failure mode we mitigate by mirroring everything we pull to local
  Parquet immediately.

## Consequences

- `src/supertrader/data/sources/reddit_arctic_shift.py::ArcticShiftPostsSource`
  is the concrete `DataSource` for Reddit history. It satisfies the `DataSource`
  protocol from `data/base.py` and partitions on `(subreddit, year_month)`.
- A separate `ArcticShiftCommentsSource` is deferred to Phase 2 — same shape, a
  different endpoint (`/api/comments/search`) and schema column `body` instead
  of `title`/`selftext`.
- Hard-failure recovery for the API requires fallback to per-subreddit torrent
  download (no such dump exists today; we would have to filter the all-Reddit
  monthly archives). That is a Phase 3 contingency only.

## Hard-gate verdict

Week 1 Reddit-historical gate: **PASS**. Proceed to Week 2 (config loader,
`PointInTimeStore`, layer-boundary tests).
