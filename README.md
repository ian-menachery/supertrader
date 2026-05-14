# supertrader

Personal quantitative research platform. First strategy: Reddit-sentiment mean-reversion on US equities. Framework is strategy-agnostic — add a new strategy by writing a config file plus a small `Strategy` subclass.

## Status

Week 1 scaffolding. Not yet runnable end-to-end.

## Architecture

Four strict layers with one-way imports:

```
data ──▶ signals ──▶ strategies ──▶ execution
                       (pipelines compose them)
```

- **data**: DataSources ingest external data into a canonical Parquet store. Never read by strategies directly.
- **signals**: Pure functions of stored data. Produce `(date × ticker → float)` panels.
- **strategies**: Consume one or more signals, emit target weights.
- **execution**: Translate target weights to orders (backtest, paper, live).

Layer boundaries are enforced by `import-linter` in CI.

## Install

```powershell
uv sync
uv run pre-commit install
```

## Quickstart

Not yet wired. Coming end of Week 4.

```powershell
# Eventual usage:
uv run supertrader data refresh --source yfinance_prices
uv run supertrader signals compute --name reddit_sentiment_v1
uv run supertrader backtest --config configs/runs/rsm_v1_backtest.yaml
```

## Development

```powershell
uv run ruff check
uv run ruff format
uv run mypy src/supertrader
uv run pytest
uv run lint-imports
```

## Project layout

See `docs/adr/` for architecture decision records and the build plan at `~/.claude/plans/i-m-building-a-personal-sorted-kettle.md`.

## License

Personal project. UNLICENSED.
