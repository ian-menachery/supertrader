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

## 2026-05-14 — v2 tech cycle: three signals, three negative verdicts

The "build Form 4 on free data" plan exited at its first gate —
`~/projects/redline` only has 170 Form 4 rows across 5 issuers,
nowhere near a cross-sectional study. Pivoted again: drop Form 4 and
Reddit-as-signal-source entirely; focus the signal layer on
technical indicators (price action + volume) which the existing
data + framework already support.

ADRs amended for the pivot:
- ADR 0003 (redline boundary) → *Superseded; redline not used*.
- ADR 0006 (sentiment scorer) → *Shipped, not in active development*.
- ADR 0008 (paid data) → *Accepted; execution deferred per cost-
  consciousness review* with explicit Polygon/EODHD trigger criteria.

Built:
- `configs/universe/snapshot_sp500_2026_05_14.csv` — 503 SP500 names
  from the datahub.io github source (Wikipedia blocks scripted
  scrapes by default).
- Backfilled yfinance OHLCV for the SP500 universe over 2018-2025
  (~1M rows; faster than expected, ~15 min wall-clock).
- `src/supertrader/signals/technical/{momentum,reversal,
  volume_surge}.py` — three signal modules + tests (19 new unit
  tests + 6 lookahead-regression tests).
- Three v2 configs: `v2_tech_momentum`, `v2_tech_reversal`,
  `v2_tech_volume_surge`.
- Per-config `snapshot_path` support added to `UniverseConfig` with a
  `field_validator(mode="before")` to coerce str→Path (Pydantic
  strict mode otherwise rejects the YAML string).

Results — all three negative:

  | signal       | TRAIN Sharpe | TEST Sharpe | TEST IR vs SPY | Turnover |
  | momentum     | -0.06        | -0.89       | -1.76          | 13×      |
  | reversal     | -2.05        | -3.01       | -2.90          | 219×     |
  | volume_surge | -0.67        | +0.89       | -0.38          | 165×     |

Volume surge's +0.89 test Sharpe is the best of the three but
exhibits the same anti-generalization pattern from rsm_v1 (negative
train) and an IR vs SPY of -0.38 (underperforms the benchmark). Plus
N=7 bonferroni → Sharpe > ~1.6 threshold, which nothing clears.

Decision: no holdout touch, no cost-sensitivity sweeps, no paid-data
subscription. Documented in `docs/verdicts/v2-tech-comparison.md`
and `docs/postmortem/v2-tech.md`. README status table updated.

The framework's third independent verdict cycle (v2 tech ÷ 3
signals) ran without architectural changes — discipline machinery
worked correctly end-to-end. Framework is at this point the
project's primary deliverable.

## 2026-05-14 — platform-honesty pass + project lock-in

After cycle 2 closed with three more null results, the user pushed
back hard on "what to improve in the signals" framing: iterating on
failed signals is the discipline failure mode HoldoutGuard exists to
prevent. The right move is platform-level improvements that apply to
every future strategy without burning peeks, then lock in the
lessons.

Shipped this session, zero new test-set peeks consumed:

- **P4 — universe-guard:** `MeanReversionStrategy` now filters NaN-
  price tickers from each day's ranking cross-section. The
  cross-section is no longer silently contaminated by tickers
  outside the tradeable universe on that date. The fix surfaces a
  real leakage: existing rsm_v1_q1_2024 re-runs produce slightly
  different numbers (~1% drift on Sharpe) because the NaN-price
  exclusion changes the rank distribution.
- **P1 — `max_turnover_annual`:** opt-in cap on per-day turnover.
  Soft-clip per the plan. Prevents future strategies from producing
  silently-absurd turnover (v2 reversal hit 219×).
- **P2 — `smoothing_alpha`:** EMA on weights, default 1.0 (no-op).
  Lower alpha forces signals to persist before driving trades.
- **P3 — cost model v2 (ADR 0010):** `costs.model_version` field
  with v1/v2 dispatch. v1 keeps `slippage_bps_base` (3 bps default).
  v2 uses `half_spread_bps` (5 bps default, stricter). Engine wired
  via new `flat_slippage_fraction` helper; the existing
  per-cell impact path in `slippage.py` is reserved for v2.1 once
  ADV data flows through.
- **P5 — historical config pins:** all rsm_v1 + v2-tech +
  smoke configs explicitly set `costs.model_version: v1` so their
  numbers stay reproducible under the new code path.
- **W1 — `docs/retrospective.md`:** single-document arc of the
  project. README links to it at the top of the status section.
- **W2 — README:** status table flips to include the platform-
  honesty pass; "for a single-document read see retrospective."
- **W3 — CLAUDE.md lessons:** six new rules codified from this
  cycle, including the "iterating on a failed signal is the
  discipline failure mode" rule that triggered this whole pass.

Tests: 400 passing / 1 skipped. ruff / ruff-format / mypy --strict /
import-linter all green.

State at session close:
- N = 7 cumulative test-set peeks. Bonferroni threshold ~Sharpe 1.6.
- All four holdouts untouched.
- Four documented null results, each with verdict + postmortem.
- Platform now has explicit turnover cap, weight smoothing, v2 cost
  model, universe-guard, and a project-wide retrospective.
- No paid data subscribed; ADR 0008 trigger criteria not met.

Next research cycle (no concrete date) should:
- Start with `model_version: v2` (default).
- Cite N = 7 as the carried bonferroni cost.
- Either activate paid data (Polygon/EODHD per ADR 0008) or open a
  redline-backfill plan (Form 4) — not both simultaneously.
- Read this retrospective + CLAUDE.md lessons before designing the
  first config.

---

## 2026-05-15 — Sector decomposition + rule-based strategy infrastructure

Built three pieces of platform machinery — zero new test-set peeks. N
stays at 7.

**S3 — three indicator signal classes** (compose with the new strategy
in S2):

- `signals/technical/percent_change.py` — N-day percent change
  `close[T] / close[T-N] - 1`. Enables rules like "long when stock
  drops 2%" via `SignalThresholdStrategy(long_entry=-0.02)`.
- `signals/technical/ma_cross.py` — `(fast_ma - slow_ma) / slow_ma`.
  Default 20d/50d; positive = uptrend.
- `signals/technical/rsi.py` — Wilder's 14-day RSI rescaled to
  `[-1, 1]`. Threshold `-0.4` ≈ RSI < 30 (oversold).
  - Subtle correctness fix: monotonic-up case (`avg_loss == 0` and
    `avg_gain > 0`) returns RSI 100 explicitly, not NaN. Same for the
    symmetric monotonic-down case. Both-zero stays NaN.
- All three appear in `tests/unit/test_technical_lookahead.py`'s
  parametrized regression guard — 12 tests covering "signal at day T
  must not read prices > T" across the whole technical-signals layer.

**S2 — `SignalThresholdStrategy` (per-ticker time-series strategy)**:

- `strategies/threshold.py` — registered as
  `@strategies.register("signal_threshold")`.
- Per-ticker state machine: flat → long (`signal > long_entry`),
  flat → short (`signal < short_entry`, omit for long-only),
  long → flat (`signal < exit_threshold`),
  short → flat (`signal > -exit_threshold`).
- Exit logic runs before entry logic each day — no same-day flips.
- `max_positions` cap selects largest `|signal|` among eligible
  entries; held positions are kept ahead of new entries.
- NaN-price → skip entry (matches the universe-guard contract added
  in the platform-honesty pass). NaN-signal on a held day → hold,
  not flatten.
- Reuses `apply_position_persistence` (smoothing + turnover cap) and
  `scale_to_gross` from `strategies/risk.py`.
- Helper extraction: `_apply_position_persistence` and
  `_ANNUALIZATION` were duplicated between `MeanReversionStrategy`
  and the new class. Promoted both to `strategies/risk.py` as
  `apply_position_persistence` + `ANNUALIZATION_DAILY: int = 252`.
  `MeanReversionStrategy` now imports the shared helper.

**S1 — sector decomposition diagnostic** (re-analysis tool, never a
re-run):

- `backtest/sector_decomp.py` — `decompose_by_sector(weights, prices,
  ticker_to_sector, *, execution_delay_bars=1)` returns
  `{sector: SectorContribution}` with cumulative return, Sharpe,
  Sortino, MaxDD, and mean gross exposure per sector. Reuses
  `backtest.metrics` for the underlying stat computations — no new
  metric code.
- `scripts/decompose_by_sector.py` — CLI wrapper. Reruns a config
  **in-process** so the resulting `config_hash` matches the original
  run; the `HoldoutGuard` would refuse a fresh peek anyway. Prints a
  per-sector table sorted by cum_return descending; writes
  `data/runs/<run_id>/sector_decomp.md` if the run dir is on disk.
- Unmapped tickers bucket into `"Unknown"` rather than being silently
  dropped — same honesty principle as the universe-guard.
- Zero-exposure sectors are omitted from output (won't show
  "Energy: 0.0" rows for sectors the strategy never touched).

**Pipeline wiring**:

- `pipelines/run_backtest.py:_build_signal` gains
  `percent_change` / `ma_cross` / `rsi` branches.
- `_build_strategy` gains `signal_threshold` branch; return type
  widened to `MeanReversionStrategy | SignalThresholdStrategy`;
  `_run_one_window` widened to the base `Strategy` ABC.

**Discipline note (carried forward, not modified):**

This work adds capability but does NOT run any new backtest. The
moment a `SignalThresholdStrategy` config gets executed against
prices, that's an explicit peek with an explicit bonferroni cost —
the plan that authorizes such a run must cite `N` and justify the
threshold. The CLAUDE.md rule still holds: if a new custom-rule
strategy produces another null result on this same SP500 + window,
write the postmortem and switch strategy class. Do not twiddle the
thresholds.

Tests: all new modules covered. ruff / ruff-format / mypy --strict /
import-linter / pytest gates green at session close.
