"""End-to-end backtest pipeline: config -> signal -> strategy -> engine -> metrics.

This is the only place in the codebase that imports cross-layer. It owns the
"plug things together" responsibility. Concrete factories for each layer are
inline (v1 pragmatism) — full registry-driven plugin discovery is Phase 2 when
we have more than one concrete strategy + signal pair.

The pipeline always runs train+test by default. To touch the holdout you must
pass `include_holdout=True`, which then goes through `HoldoutGuard` — second
touch with the same config hash raises.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import polars as pl

from supertrader.backtest.engine import BacktestResult, VectorbtEngine
from supertrader.backtest.report import render_tear_sheet
from supertrader.backtest.splits import HoldoutGuard, TrainTestHoldoutSplit
from supertrader.config.loader import load_run_config
from supertrader.config.schemas import RunConfig
from supertrader.data.calendar import TradingCalendar
from supertrader.data.store import ParquetStore, PITStoreView
from supertrader.data.universe import StaticUniverse
from supertrader.observability.run_manifest import (
    RunManifest,
    config_hash,
    hash_input_partitions,
    manifest_to_row,
    start_manifest,
)
from supertrader.signals.reddit_sentiment.scorer_vader import VaderScorer
from supertrader.signals.reddit_sentiment.signal import RedditSentimentSignal
from supertrader.signals.reddit_sentiment.ticker_extract import load_blocklist
from supertrader.strategies.mean_reversion import MeanReversionStrategy

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
INPUT_SOURCE_IDS: tuple[str, ...] = ("yfinance.prices.daily", "arctic_shift.posts")


@dataclass(frozen=True)
class BacktestRunOutput:
    """Top-level pipeline output: backtest result plus reproducibility metadata."""

    config: RunConfig
    config_hash: str
    train_result: BacktestResult
    test_result: BacktestResult
    holdout_result: BacktestResult | None
    metrics_path: Path
    manifest: RunManifest
    tear_sheet_path: Path


def _build_signal(
    config: RunConfig, universe: set[str], blocklist: set[str], repo_root: Path
) -> tuple[str, RedditSentimentSignal]:
    """Build the (single) signal from the run config. v1 supports `reddit_sentiment` only."""
    if len(config.signals) != 1:
        msg = f"v1 pipeline supports exactly one signal; got {len(config.signals)}"
        raise NotImplementedError(msg)
    sig_cfg = config.signals[0]
    if sig_cfg.type != "reddit_sentiment":
        msg = f"v1 pipeline supports signal type 'reddit_sentiment'; got '{sig_cfg.type}'"
        raise NotImplementedError(msg)
    params = sig_cfg.params
    scorer_cfg = params.get("scorer", {})
    scorer_type = scorer_cfg.get("type", "vader")
    if scorer_type != "vader":
        msg = f"v1 pipeline supports scorer 'vader' only; got '{scorer_type}'"
        raise NotImplementedError(msg)
    lexicon_path_raw = scorer_cfg.get("params", {}).get(
        "lexicon_path", "configs/sentiment_lexicon.yaml"
    )
    lexicon_path = repo_root / lexicon_path_raw
    scorer = VaderScorer(lexicon_path)
    signal = RedditSentimentSignal(
        scorer=scorer,
        universe=universe,
        aggregation=params.get("aggregation", "score_weighted_mean"),
        decay_halflife_hours=float(params.get("decay_halflife_hours", 24.0)),
        sources=tuple(params.get("sources", ["arctic_shift.posts"])),
        blocklist=blocklist,
    )
    return sig_cfg.name, signal


def _build_strategy(config: RunConfig) -> MeanReversionStrategy:
    """Build the strategy. v1 supports `mean_reversion` only."""
    if config.strategy.type != "mean_reversion":
        msg = f"v1 pipeline supports strategy 'mean_reversion'; got '{config.strategy.type}'"
        raise NotImplementedError(msg)
    params = config.strategy.params
    if not config.strategy.signals:
        msg = "Strategy must reference at least one signal"
        raise ValueError(msg)
    return MeanReversionStrategy(
        signal_name=config.strategy.signals[0],
        quantile=float(params.get("quantile", 0.3)),
        min_signal_observations=int(params.get("min_signal_observations", 5)),
        target_gross=float(params.get("target_gross", 1.0)),
    )


def _load_prices(store: ParquetStore, start: date, end: date) -> pd.DataFrame:
    """Pivot the canonical OHLCV store into a (date x ticker) close-price DataFrame."""
    df = (
        store.scan("yfinance.prices.daily")
        .filter(pl.col("date") >= start)
        .filter(pl.col("date") <= end)
        .select(["date", "ticker", "close"])
        .collect()
    )
    pdf = df.to_pandas().pivot(index="date", columns="ticker", values="close")
    pdf.index = pd.to_datetime(pdf.index, utc=True)
    pdf.index.name = "date"
    return pdf


def _run_one_window(
    *,
    signal_name: str,
    signal: RedditSentimentSignal,
    strategy: MeanReversionStrategy,
    engine: VectorbtEngine,
    store: ParquetStore,
    start: date,
    end: date,
    universe_list: list[str],
    initial_capital: float,
    execution_delay_bars: int,
) -> BacktestResult:
    """Compute the signal, ask the strategy for weights, run the engine."""
    pit_view = PITStoreView(store, as_of=end)
    log.info("computing signal %s..%s", start, end)
    signal_panel = signal.compute(pit_view, start, end, universe_list)
    log.info("loading prices %s..%s", start, end)
    prices = _load_prices(store, start, end)
    if prices.empty:
        msg = f"No prices on disk for {start}..{end}"
        raise RuntimeError(msg)
    log.info("computing target weights")
    target_weights = strategy.target_positions({signal_name: signal_panel}, prices)
    log.info("running engine")
    return engine.run(
        target_weights,
        prices,
        initial_capital=initial_capital,
        execution_delay_bars=execution_delay_bars,
    )


def _persist_manifest(store: ParquetStore, manifest: RunManifest, run_dir: Path) -> None:
    """Mirror a manifest to SQLite + a JSON file under `run_dir`."""
    row = manifest_to_row(manifest)
    store.upsert_run_manifest(
        run_id=row[0],
        config_path=row[1],
        config_hash=row[2],
        git_sha=row[3],
        python_version=row[4],
        started_at=row[5],
        ended_at=row[6],
        status=row[7],
        data_hashes_json=row[8],
    )
    manifest.write_json(run_dir / "manifest.json")


@dataclass(frozen=True)
class _PipelineWindows:
    train: BacktestResult
    test: BacktestResult
    holdout: BacktestResult | None


def _execute_windows(
    *,
    config: RunConfig,
    cfg_hash: str,
    store: ParquetStore,
    data_dir: Path,
    universe_path: Path,
    blocklist_path: Path,
    repo_root: Path,
    include_holdout: bool,
) -> _PipelineWindows:
    """Build strategy + signal + engine and execute each train/test/holdout window."""
    universe_loader = StaticUniverse.from_csv(universe_path)
    universe_set = set(universe_loader.tickers())
    universe_list = list(universe_set)
    blocklist = load_blocklist(blocklist_path)

    signal_name, signal = _build_signal(config, universe_set, blocklist, repo_root)
    strategy = _build_strategy(config)
    engine = VectorbtEngine(config.backtest.costs)

    calendar = TradingCalendar()
    split = TrainTestHoldoutSplit.from_config(config.backtest, calendar)
    del split  # informational; per-window dates come from BacktestConfig directly

    def _run(start: date, end: date) -> BacktestResult:
        return _run_one_window(
            signal_name=signal_name,
            signal=signal,
            strategy=strategy,
            engine=engine,
            store=store,
            start=start,
            end=end,
            universe_list=universe_list,
            initial_capital=config.backtest.initial_capital,
            execution_delay_bars=config.backtest.execution_delay_bars,
        )

    log.info("=== TRAIN ===")
    train_result = _run(config.backtest.start, config.backtest.train_end)
    log.info("=== TEST ===")
    test_result = _run(config.backtest.train_end, config.backtest.test_end)

    holdout_result: BacktestResult | None = None
    if include_holdout:
        meta_db = data_dir / "meta.sqlite"
        guard = HoldoutGuard(meta_db)
        log.warning("EVALUATING HOLDOUT — this is permanent for config_hash=%s", cfg_hash[:16])
        guard.evaluate(config.run_id, cfg_hash)
        log.info("=== HOLDOUT ===")
        holdout_result = _run(config.backtest.test_end, config.backtest.end)

    return _PipelineWindows(train=train_result, test=test_result, holdout=holdout_result)


def _write_metrics_json(
    *, run_dir: Path, config: RunConfig, cfg_hash: str, windows: _PipelineWindows
) -> Path:
    metrics_payload = {
        "run_id": config.run_id,
        "config_hash": cfg_hash,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "train": windows.train.metrics,
        "test": windows.test.metrics,
        "holdout": windows.holdout.metrics if windows.holdout is not None else None,
    }
    metrics_path = run_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(metrics_payload, f, indent=2, default=str)
    log.info("metrics written to %s", metrics_path)
    return metrics_path


def run_backtest(
    config_path: Path | str,
    *,
    include_holdout: bool = False,
    data_dir: Path | None = None,
    universe_path: Path | None = None,
    blocklist_path: Path | None = None,
    allow_dirty: bool = False,
) -> BacktestRunOutput:
    """Execute a backtest end-to-end given a YAML config path.

    On a dirty git tree, refuses to run unless `allow_dirty=True`. Every run
    writes a `RunManifest` to both `meta.sqlite` and `data/runs/<run_id>/
    manifest.json`, then renders `tear_sheet.html` alongside `metrics.json`.
    """
    repo_root = REPO_ROOT
    data_dir = data_dir or (repo_root / "data")
    if universe_path is None:
        universe_path = repo_root / "configs" / "universe" / "snapshot_2026_05_14.csv"
    blocklist_path = blocklist_path or (repo_root / "configs" / "ticker_blocklist.yaml")

    config = load_run_config(config_path)
    cfg_hash = config_hash(config)
    log.info("run_id=%s config_hash=%s", config.run_id, cfg_hash)

    store = ParquetStore(data_dir)
    run_dir = data_dir / "runs" / config.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = start_manifest(
        run_id=config.run_id,
        config_path=Path(config_path),
        config_hash_hex=cfg_hash,
        repo_root=repo_root,
        allow_dirty=allow_dirty,
    )
    _persist_manifest(store, manifest, run_dir)
    if manifest.git_dirty:
        log.warning("running with --allow-dirty; git_dirty=True recorded on manifest")

    try:
        windows = _execute_windows(
            config=config,
            cfg_hash=cfg_hash,
            store=store,
            data_dir=data_dir,
            universe_path=universe_path,
            blocklist_path=blocklist_path,
            repo_root=repo_root,
            include_holdout=include_holdout,
        )
        metrics_path = _write_metrics_json(
            run_dir=run_dir, config=config, cfg_hash=cfg_hash, windows=windows
        )

        data_hashes = hash_input_partitions(store.root, list(INPUT_SOURCE_IDS))
        manifest = manifest.with_status(
            status="ok", ended_at=datetime.now(tz=UTC), data_hashes=data_hashes
        )
        _persist_manifest(store, manifest, run_dir)

        tear_sheet_path = render_tear_sheet(
            train=windows.train,
            test=windows.test,
            holdout=windows.holdout,
            manifest=manifest,
            out_path=run_dir / "tear_sheet.html",
        )
        log.info("tear sheet written to %s", tear_sheet_path)

    except Exception:
        manifest = manifest.with_status(status="failed", ended_at=datetime.now(tz=UTC))
        _persist_manifest(store, manifest, run_dir)
        raise
    else:
        train_result = windows.train
        test_result = windows.test
        holdout_result = windows.holdout

    return BacktestRunOutput(
        config=config,
        config_hash=cfg_hash,
        train_result=train_result,
        test_result=test_result,
        holdout_result=holdout_result,
        metrics_path=metrics_path,
        manifest=manifest,
        tear_sheet_path=tear_sheet_path,
    )
