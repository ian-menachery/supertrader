# supertrader

Personal quantitative research platform. First strategy: Reddit-sentiment
mean-reversion on US equities. The framework is strategy-agnostic — new
strategies are config files plus a small `Strategy` subclass.

> **This is exploratory research code. DO NOT use it to trade real capital.**
> See [LICENSE](LICENSE) and [`docs/known-limitations.md`](docs/known-limitations.md)
> for the full caveats.

## Status

First research cycle complete. The framework is shipped end-to-end and the
first strategy idea has been evaluated honestly.

| Phase | Status |
| ----- | ------ |
| Data layer (Polars + Parquet store, point-in-time view) | done |
| Reddit-sentiment signal (VADER + ticker extractor) | done |
| vectorbt engine + costs + metrics + `MeanReversionStrategy` | done |
| HoldoutGuard + one-shot holdout discipline | done |
| HTML tear sheet + `RunManifest` reproducibility ledger | done |
| 2-year WSB backfill (2022 → Q1 2024, ~415K posts) | done |
| Canonical 18mo/6mo backtest + cost sensitivity + diagnostics | done |
| **Honest verdict on RSM v1** | **negative — see postmortem** |
| SPY benchmark wired (beta + IR on every tear sheet) | done |
| Paper trading via Alpaca, Form 4 integration sketch | future, pending a working strategy |

**Result so far:** RSM v1 (Reddit-sentiment mean-reversion) produced a
test-window Sharpe of ~0.94 but a *negative* train Sharpe of -0.47, a 2×
cost test Sharpe of 0.68 (below the 0.8 tradeable threshold), and an
information ratio over SPY of only +0.34. The signal is regime-dependent
noise, not a stable edge. The holdout slot remains untouched.

Full reasoning:
- [Verdict](docs/verdicts/rsm-v1-backtest.md) — initial response to the
  canonical run.
- [Postmortem](docs/postmortem/rsm-v1.md) — considered analysis after
  cost-sensitivity, momentum-variant, and Q3/Q4 decomposition.
- [Known limitations](docs/known-limitations.md) — eight ranked caveats
  that bound any result here.

330+ tests, ~94% line coverage, mypy `--strict` clean, import-linter
enforced layer boundaries.

## Architecture

Four strict layers, one-way imports:

```
data ──▶ signals ──▶ strategies ──▶ execution
                       (pipelines compose them)
```

- **data** — `DataSource`s ingest external data into a canonical Parquet
  store (`ParquetStore` + sqlite metadata). Never read by strategies
  directly. Sources are write-only.
- **signals** — Pure functions of stored data. Produce `(date × ticker)`
  panels.
- **strategies** — Consume named signals, emit target weights.
- **execution / backtest** — Translate target weights to orders (backtest
  via vectorbt; paper trading via Alpaca planned).

`import-linter` enforces the layering. ADRs in `docs/adr/` record the
non-obvious decisions: vectorbt as the engine (0001), Arctic Shift for
Reddit history (0002), redline as a clean Parquet-export boundary (0003),
static universe and survivorship caveat (0004), pluggable sentiment scorer
(0006).

## Install

```powershell
uv sync
uv run pre-commit install
```

## Quickstart

```powershell
# Q1 2024 framework-validation smoke (3-month window — NOT a strategy verdict).
# Requires a clean git tree; pass --allow-dirty to override.
uv run supertrader backtest --config configs/runs/rsm_v1_q1_2024.yaml

# Output lands in data/runs/rsm-v1-q1-2024-smoke/:
#   metrics.json     — sharpe / sortino / drawdown per train/test/holdout
#   manifest.json    — git SHA, config hash, data hashes (reproducibility)
#   tear_sheet.html  — open in a browser
```

The canonical 18mo train / 6mo test / 3mo holdout config is
`configs/runs/rsm_v1_backtest.yaml`. It needs the full 2022-2024 WSB backfill
to be on disk; that backfill is running at the time of this README.

## Development

```powershell
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src/
uv run lint-imports
uv run pytest                # ~1.5 min
```

[`CLAUDE.md`](CLAUDE.md) is loaded automatically by Claude Code sessions in
this repo — it codifies the conventions (layered architecture, strict typing,
holdout discipline, no DB mocking, etc.).

[`docs/dev-notes.md`](docs/dev-notes.md) is the running engineering journal.

## Reproducibility

Every backtest run writes two sources of truth:

- `data/meta.sqlite` — `run_manifests` table with run id, config hash, git
  SHA, dirty flag, python + supertrader versions, started/ended timestamps,
  status, and a content-hash dict of every input parquet partition.
- `data/runs/<run_id>/manifest.json` — same data, mirrored as JSON for
  git-able provenance next to the tear sheet.

`run_backtest()` refuses to start on a dirty git tree unless `--allow-dirty`
is passed (the dirty flag is recorded on the manifest either way).

The holdout window is one-shot per `config_hash`. A second touch raises
`HoldoutTouchedError`. The only way to re-evaluate is
`scripts/reset_holdout_lock.py`, which appends an audit record to
`data/runs/holdout_overrides.log` (append-only, pre-commit-protected).

## Honesty about results

Treat any reported Sharpe ratio in this repository as exploratory until the
canonical 18mo/6mo/3mo run is complete and reviewed in light of
[`docs/known-limitations.md`](docs/known-limitations.md). The shortlist of
caveats:

- The universe is selection-biased toward stocks WSB talks about.
- Survivorship bias on this 34-ticker subset is plausibly 3-8%/yr, not 1-3%.
- The cost model likely understates round-trip costs by 50-200%.
- The 3-month holdout cannot validate a moderate signal — only reject
  extreme overfitting.

A positive backtest is the *start* of a research question, not its end.

## Project layout

```
src/supertrader/      # importable package (src layout)
  config/             # pydantic config models + YAML loader
  data/               # ParquetStore, sources, universe, point-in-time view
  signals/            # signal base + reddit_sentiment + technical
  strategies/         # strategy base + mean_reversion + risk
  backtest/           # vectorbt engine, costs, metrics, splits, report
  execution/          # backtest adapter (paper/live planned)
  pipelines/          # run_backtest, the only module that crosses layers
  observability/      # RunManifest + structured logging
  cli.py              # typer entry point
configs/              # version-controlled YAML configs
  runs/               # top-level run configs (compose data + signal + strategy)
  universe/           # ticker snapshots
data/                 # gitignored — on-disk parquet store, sqlite metadata, runs
docs/
  adr/                # architecture decision records
  known-limitations.md
  dev-notes.md
scripts/              # standalone ops scripts (backfill, validation, reset)
tests/
  unit/               # fast, isolated
  integration/        # touches disk + small fixtures
  e2e/                # full pipeline on tiny synthetic data
  golden/             # snapshots + reference data
research/             # throwaway notebooks (not importable)
```

Canonical 8-week build plan: `~/.claude/plans/i-m-building-a-personal-sorted-kettle.md`.

## License

See [LICENSE](LICENSE). All rights reserved; research use only. Not for
trading.
