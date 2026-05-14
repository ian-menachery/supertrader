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
