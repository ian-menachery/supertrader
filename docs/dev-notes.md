# dev-notes.md

Append-only chronological log of decisions made during coding that are too
small for an ADR and too important to lose. One section per session.

**Format conventions:**
- Date heading: `## YYYY-MM-DD — short summary`
- Past-tense voice. "Decided X, because Y." "Hit Z, fixed by..."
- Bullet points over paragraphs.
- Link to file paths with line numbers (`splits.py:84`) and to ADRs by
  number where relevant.
- If a decision later proves wrong, do not edit the original entry. Add a
  new entry that supersedes it and links back.

Larger architectural choices belong in `docs/adr/` as their own ADR.
Honest caveats about results or methodology belong in
`docs/known-limitations.md`.

---

## 2026-05-14 — Week 5 ship + streaming refactor + backfill kickoff

Shipped the Week 5 framework deliverables in five commits on `main`:

- `feat(observability)` — RunManifest reproducibility ledger. Writes to
  `data/meta.sqlite` and `data/runs/<run_id>/manifest.json`. Pipeline
  refuses on dirty git tree unless `--allow-dirty` is passed. Config-hash
  computation moved into `observability/run_manifest.py` so the pipeline
  imports it from one place.
- `feat(backtest)` — HTML tear sheet (`backtest/report.py` + Jinja
  template), three matplotlib PNGs base64-embedded. Survivorship-bias
  warning text consolidated into `data/universe.py:SURVIVORSHIP_WARNING`
  so the template, ADR 0004, and any future consumer all read from one
  source.
- `feat(backtest)` — HoldoutOverrideLog (JSON-Lines, fsynced),
  `HoldoutGuard.clear()`, `scripts/reset_holdout_lock.py`,
  `scripts/check_holdout_log_untouched.sh`. Reset script refuses on dirty
  tree (no escape hatch — overrides must be reproducible).
- `style:` — bundle ruff-format-only diffs on unrelated files. Kept
  separate from feature commits so the bisect log stays clean.
- `fix(test):` — dropped `out.manifest.git_dirty is True` from the e2e
  smoke. The assertion was developer-state-specific (held only while the
  W5 work was uncommitted).

**Notable design calls captured here:**

- Tear-sheet snapshot test compares a *normalized JSON* of the rendered
  HTML (sections, metric rows, monthly returns, PNG-size sanity floors)
  via `deepdiff`. We do not compare raw PNG bytes — matplotlib's output
  varies subtly across versions/platforms and would make the test flaky.
  Regenerate with `UPDATE_GOLDEN=1`.
- `RunManifest` keeps observability layer-clean by taking
  `_SupportsModelDumpJson` protocol instead of importing
  `supertrader.config.schemas.RunConfig`. The layered-architecture
  contract forbids `observability → config`, even via `TYPE_CHECKING`.
- `_blake2b_file` is duplicated in `observability/run_manifest.py` rather
  than imported from `data/store.py:108` for the same layer-clean reason.
  The cost is ~5 lines of duplicated code.

**Streaming refactor (`reddit_arctic_shift.py`):**

- Replaced the all-in-memory `fetch()` accumulator with `fetch_months()`,
  a generator yielding `(subreddit, year_month, LazyFrame)` per
  non-empty month. Memory ceiling is one month (~25K WSB posts ≈ 250 MB
  peak) rather than the prior ~1.7 GB OOM.
- `ingest()` now calls `store.write()` per yield. `ParquetStore.write` is
  already atomic per partition, so a crash mid-backfill leaves a
  consistent prefix of months on disk; resuming runs the same range
  idempotently.
- `fetch()` retained as a compatibility wrapper for tests that want a
  single materialized frame over a small window.
- Added a `_RecordingStore` fake in
  `tests/integration/test_reddit_arctic_shift.py` to assert the
  per-(subreddit, year_month) write count, partition keys, and
  empty-month-skip behavior.

**Backfill kicked off:**

- `uv run python scripts/backfill_wsb.py --start 2022-02-01 --end 2024-01-01`
  running in the background.
- 2022-01 was already on disk from the single-month memory probe
  (24,109 rows, no OOM — refactor verified against live API).
- ETA roughly 5-6 hours at ~14 min/month observed pacing. Notification
  will fire on completion.

**Out of scope / parked:**

- Canonical re-run of `configs/runs/rsm_v1_backtest.yaml` — blocked on
  backfill.
- Acting on any item in `docs/known-limitations.md`. Each is a separate
  future plan.
- PRAW live ingest, FinBERT scorer, 500-post sentiment eval set — second-
  order improvements; don't change the gating question.

**Repo hygiene shipped today:**

- `CLAUDE.md` at repo root — project conventions for every Claude Code
  session.
- `docs/known-limitations.md` — eight ranked caveats, intended as
  required reading before drawing conclusions from any tear sheet.
- This file — engineering journal seeded with this entry.
- Repo pushed public to `github.com/ian-menachery/supertrader`.

**Canonical re-run + verdict (post-backfill):**

- Backfill completed in ~1h 44min (much faster than my 5-6h estimate;
  2023 months were lighter than 2022). 27 WSB partitions, 415K posts.
- Canonical `rsm_v1_backtest.yaml` (1×) + `_2x_cost.yaml` + `_3x_cost.yaml`
  all ran on the freshly-backfilled data.
- Result pattern is unusual: **train Sharpe -0.48, test Sharpe +1.34**
  at 1× cost. Anti-generalization — train lost money for 18 months and
  then test made money for 6. Limitation-#3 decision tree branches to
  *cost-sensitive but interesting* (2× test Sharpe 0.57 < 0.8 threshold),
  but the negative-train pattern argues even more strongly against
  treating this as a real signal.
- Honest read landed in `docs/verdicts/rsm-v1-backtest.md`: not
  tradeable; do not touch the holdout; next move is universe
  randomization to test the selection-bias hypothesis (limitation #1).
- ADR 0005's discipline holds: holdout untouched, no post-hoc parameter
  sweep, no second test-set peek with a tuned config.
