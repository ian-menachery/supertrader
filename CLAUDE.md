# CLAUDE.md — supertrader

Loaded automatically into every Claude Code session in this repo.

## Project context

`supertrader` is a personal quant research platform. First strategy is a
Reddit-sentiment mean-reversion signal on US equities; the framework is
strategy-agnostic, so future strategies (Form 4 insider clustering, options
flow, technicals, multi-factor combos) are config-only changes — no
framework rewrites.

The bar is **honest backtests, strict typing, and reproducibility** — not
features. A negative strategy result is a real result; document it and move on.

- Canonical 8-week build plan: `~/.claude/plans/i-m-building-a-personal-sorted-kettle.md`
- Known limitations & honest caveats: `docs/known-limitations.md` (read this before reasoning about results)
- Architecture decision records: `docs/adr/`
- Engineering journal: `docs/dev-notes.md`

## Toolchain

- Python 3.12 (pinned in `.python-version`)
- `uv` for dep management. **Always `uv run <cmd>`**, never bare `python`.
- `ruff` for lint + format (strict — see `pyproject.toml`)
- `mypy --strict` (no exceptions; if matplotlib needs `# type: ignore`, that's fine, just don't disable strict)
- `pytest` + `pytest-cov` (80% coverage floor, enforced)
- `import-linter` for layered-architecture enforcement
- `vectorbt` is the backtest engine (ADR 0001). Polars in the data layer,
  pandas at the strategy→engine boundary.
- Pre-commit hooks live in `.pre-commit-config.yaml`; install via
  `uv run pre-commit install`.

## Architectural invariants

Enforced by `import-linter` — violations fail CI:

```
pipelines  (top)
  └── execution | backtest
        └── strategies
              └── signals
                    └── data
                          └── config | observability  (bottom)
```

Higher layers may import lower ones. Peers cannot import each other. Concretely:
- `observability` cannot import from `data`, `signals`, `strategies`, etc.
- The pipeline is the only module allowed to compose across layers.
- Sources are write-only: they `store.write(...)`, they never `store.scan(...)`.

Other invariants:
- `data/` at repo root is gitignored. The `src/supertrader/data/` package is
  the source-code data layer. In `.gitignore` use `/data/` (anchored), not
  `data/`, so the package isn't accidentally excluded.
- `research/` notebooks are throwaway. They may call into `src/` but never
  define logic. They are not importable.

## Code style

- `mypy --strict` clean. `# type: ignore[arg-type]` is acceptable at typed
  third-party API boundaries (matplotlib, pandas-stubs union types) but never
  for our own code.
- Ruff rules enabled in `pyproject.toml` include `S`, `PT`, `RET`, `TRY`,
  `PERF`, `PL`, `ANN`, `D`. Read the active set before silencing a rule.
- 80% line coverage floor (`pyproject.toml [tool.pytest.ini_options]
  --cov-fail-under=80`). `observability/*` is exempted from coverage gates
  (instrumentation), but we still write tests for it.
- Public functions get docstrings. Private helpers get **none** unless the
  *why* is non-obvious. Three-line module docstrings are fine.
- Don't add backwards-compatibility shims. This is personal code — when you
  change something, change all call sites and tests in the same commit.
- Don't auto-rename `_unused` variables, re-export removed types, or leave
  `# removed` placeholders. Delete completely.

## Test discipline

- **No mocking the database.** Use the real `ParquetStore` with `tmp_path`.
  Mocked-DB tests have produced production divergence in adjacent projects;
  the same anti-pattern is banned here.
- Holdout discipline:
  - `--include-holdout` is opt-in and **one-shot per `config_hash`**, enforced
    by `HoldoutGuard` (`src/supertrader/backtest/splits.py`).
  - Second touch raises `HoldoutTouchedError`. The only way back is
    `scripts/reset_holdout_lock.py`, which appends to a JSON-Lines log at
    `data/runs/holdout_overrides.log` and refuses on a dirty git tree.
  - The override log is protected by a pre-commit hook
    (`scripts/check_holdout_log_untouched.sh`). Direct commits to the log
    are blocked; the only sanctioned path is reset script +
    `git commit --no-verify` with justification.
- Tear-sheet snapshot test (`tests/integration/test_tear_sheet_regression.py`)
  compares a *normalized JSON* shape via `deepdiff`. We do not compare raw
  PNG bytes (matplotlib output varies subtly across versions/platforms).
  Regenerate the golden with `UPDATE_GOLDEN=1 uv run pytest <path>`.
- Golden fixtures live in `tests/golden/`.

## Reproducibility expectations

Every backtest run writes a `RunManifest` to two places:
- `data/meta.sqlite` (queryable across runs)
- `data/runs/<run_id>/manifest.json` (git-able provenance)

Manifest captures: run id, config path + hash, git SHA, `git_dirty` flag,
python version, supertrader version, started/ended timestamps, status, and a
content-hash of every input parquet partition.

- `run_backtest()` **refuses to run on a dirty git tree** unless
  `--allow-dirty` is passed. The dirty flag is recorded on the manifest
  either way.
- `config_hash` is a Blake2b hex of the validated pydantic `RunConfig` JSON.
  Same config → same hash → same holdout-guard key.

## Common commands

```powershell
uv sync                                    # install deps from uv.lock
uv run pre-commit install                  # set up hooks
uv run pytest                              # full suite (~1.5 min, 330+ tests)
uv run pytest tests/unit -x --ff           # fast unit-only
uv run supertrader backtest --config configs/runs/rsm_v1_q1_2024.yaml
uv run supertrader version                 # print installed version

# Static gate (run before every commit):
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/ && uv run lint-imports

# Regenerate the tear-sheet golden after an intentional template change:
UPDATE_GOLDEN=1 uv run pytest tests/integration/test_tear_sheet_regression.py
```

## Where things live

- **Configs:** `configs/runs/*.yaml`. Inherit via `extends: ../base.yaml`.
  - `rsm_v1_q1_2024.yaml` is the framework-validation smoke (3-month window;
    DO NOT treat the metrics as a strategy verdict).
  - `rsm_v1_backtest.yaml` is the canonical 18mo/6mo/3mo split. Needs full
    2022-2024 backfill present.
- **Ticker universe:** `configs/universe/snapshot_2026_05_14.csv` —
  hand-curated 34-ticker static snapshot. Survivorship bias is real
  (see `docs/known-limitations.md`).
- **Sentiment lexicon:** `configs/sentiment_lexicon.yaml` — financial-domain
  overlay for VADER. Accuracy is unmeasured.
- **Ticker blocklist:** `configs/ticker_blocklist.yaml` — words that look
  like tickers but aren't (CEO, EPS, etc.).
- **Data store:**
  - Prices: `data/store/yfinance/prices/daily/ticker=<T>/data.parquet`
  - Reddit: `data/store/arctic_shift/posts/subreddit=<s>/year_month=<YYYY-MM>/data.parquet`
  - Sqlite metadata: `data/meta.sqlite` (run manifests, holdout touches,
    universe snapshots, source ingest log, signal cache)
- **Run outputs:** `data/runs/<run_id>/{metrics.json,manifest.json,tear_sheet.html}`
- **Holdout override log:** `data/runs/holdout_overrides.log` — append-only,
  pre-commit-protected.

## Strict don'ts

- Don't use emojis. The user does not want them in code, in commits, or in
  responses.
- Don't add a feature flag or back-compat path when you could just change the
  code.
- Don't auto-mock external services in tests. Either use the real thing
  (against `tmp_path` for filesystem) or write an integration fixture.
- Don't trust yfinance corporate actions silently. Run
  `scripts/verify_corp_actions.py` and stash the override CSV.
- Don't add docstrings to private helpers unless the *why* is non-obvious.
- Don't rewrite logic to make a type-checker happy if there's a clean
  `# type: ignore[<code>]` available; just narrow the silence to the
  specific line.
- Don't pre-commit secrets. There is no `.env.example`; if you add one,
  populate placeholders only.

## When in doubt

Read the canonical plan at `~/.claude/plans/i-m-building-a-personal-sorted-kettle.md`,
then the relevant ADR in `docs/adr/`, then the limitations doc. If still
unclear, ask the user — the budget is 160 hours total across 8 weeks; a
two-minute question beats a half-hour of guessing.

## Lessons learned (RSM v1 cycle)

Hard-won from the first complete research cycle. Each is here because
ignoring it would have cost real time or produced a wrong conclusion.

- **Anti-generalization is not opportunity.** If train Sharpe is negative
  and test Sharpe is positive on the same config, the strategy has
  *anti-generalized* — it lost money in-sample, then got lucky
  out-of-sample. The honest read is "regime-dependent noise," not "the
  test set reveals hidden edge." A real signal looks the *other* way
  (in-sample fit, slightly worse out-of-sample). See
  `docs/postmortem/rsm-v1.md` finding F4.

- **Decompose any "good" test Sharpe before celebrating.** A 6-month test
  window with Sharpe ≈ 1.5 can be entirely one quarter of luck. Slice
  the daily returns by month or by regime *before* claiming an edge. We
  found RSM v1's +1.74 test Sharpe was ~80% from a single quarter
  (`docs/postmortem/rsm-v1.md` F2). The
  `scripts/decompose_test_quarters.py` pattern is reusable: re-execute
  the same `config_hash` in-process and slice the in-memory
  `BacktestResult.returns` — no new peek, just a different lens.

- **Beta-check every long/short before celebrating.** A long/short with
  net exposure ≈ 0 can still have meaningful beta to the market if the
  short basket is systematically higher-beta than the long basket.
  Information ratio (vs SPY) is the right metric to claim "edge over the
  benchmark." If IR < 0.5 on a 6-month window, it's noise; the SE is too
  wide.

- **Sharpe SE is roughly 1 / √(years × annualization-factor).** On a
  125-day (~0.5 year) test, the SE on a Sharpe estimate is ≈ √(2) ≈ 1.4.
  Anything in [-1, +1] is statistically zero. Anything in [0.8, 2.0]
  *might* be real, but a longer window is needed to be confident.

- **The price-DataFrame index is not deterministic across data ingests.**
  `_load_prices` in `pipelines/run_backtest.py` pivots over whatever
  tickers are in `yfinance.prices.daily`. Adding SPY to the store
  shifted the canonical-config test Sharpe from 1.74 to 0.94 because
  SPY's date coverage differs slightly from the universe tickers.
  Future fix: intersect dates with `TradingCalendar.sessions(...)`
  before pivot. Until then, any reproducibility claim has to control
  for "what was in the store at run time."

- **Don't burn the holdout slot to chase a noisy test result.**
  `HoldoutGuard` is one-shot per `config_hash`. If train+test+
  cost-sensitivity all point to noise, the holdout slot stays unspent
  for a future variant that earned it.

- **A negative result is a real result.** Document the postmortem; don't
  bury it. The point of strict discipline is so you can publish negative
  results with the same confidence as positive ones.

## Lessons learned (v2 tech cycle + platform-honesty pass)

Added 2026-05-14 after the v2 cycle landed three more null results and
the user pushback caught a discipline failure mode in the proposed
"option list" of next steps.

- **After a null result, the move is not "improve the signal" — it's
  "improve the platform and switch strategy class."** Iterating on a
  failed signal is the failure mode HoldoutGuard's psychological
  purpose was built to prevent. Every degree of freedom spent tuning
  a signal against the same data shrinks the eventual holdout's
  meaning. The right response to a sequence of nulls is to lock in
  the lessons, ship platform improvements that apply to *every*
  future strategy, and switch strategy class entirely when data
  becomes available.

- **Annualized turnover above ~100× should be a config-level error
  or smoothed away by `max_turnover_annual` / `smoothing_alpha`** —
  never a silent number on a tear sheet. `MeanReversionStrategy`
  has both knobs as of the platform-honesty pass; use them on any
  new strategy whose signal could produce day-to-day churn (most
  cross-sectional rankers).

- **Cost-model reproducibility requires explicit
  `costs.model_version` pins.** All rsm_v1 + v2-tech configs pin
  `model_version: v1` to keep their historical metrics bit-for-bit
  reproducible. New configs default to `model_version: v2` (stricter
  half-spread). If you're re-running a historical config and the
  metrics differ, check that the pin is present.

- **The cross-sectional ranker filters NaN-price tickers per date.**
  Per the platform-honesty pass's universe-guard (P4 + ADR 0012):
  a ticker with NaN price on date T is excluded from that day's
  ranking cross-section entirely. This was a real leakage fix on
  StaticUniverse runs; it's load-bearing for PITUniverse runs once
  EODHD is wired. Don't undo it.

- **Multi-comparison discipline is binding.** Cumulative test-set
  peek count carries across cycles per ADR 0005. The current N = 7
  implies a Sharpe threshold of ~1.6 to clear noise on a 1-year
  test. Any new strategy variant on the same universe + window
  inherits this bar; budget peeks like budget money.

- **"Headline-positive but train-negative" is anti-generalization,
  not opportunity.** rsm_v1 and volume_surge both had this shape.
  Both were noise-grade. If you ever see this pattern again on a new
  strategy, the discipline is to write the postmortem and switch
  strategy class — not to chase the apparent test-window edge.

- **Sector decomposition is re-analysis, never a re-run.**
  `scripts/decompose_by_sector.py --config <existing_config>`
  re-executes a finished config in-process; the resulting
  `config_hash` matches the original run, so the holdout guard sees
  no new peek (and would refuse one anyway). Use it to ask "where
  did the headline number actually come from" *after* a backtest is
  done. Do NOT use it to motivate a sector-filtered re-run of the
  same config — that's exactly the data-snooping pattern the tool's
  scope was designed to discourage.

- **Custom rule strategies (`SignalThresholdStrategy` + indicator
  signals) cost a peek the moment they touch real prices.** The
  threshold strategy + `percent_change` / `ma_cross` / `rsi` signals
  are infrastructure — no new backtest gets free. A config like
  "long when stock drops 2%, exit at flat" is a hypothesis like any
  other; running it on the SP500 1y-test window increments N and
  raises the bonferroni Sharpe bar. The plan that authorizes such
  a run must cite N and the resulting threshold.
