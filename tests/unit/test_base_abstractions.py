"""Contract tests for the five layer base classes.

Each test subclasses or implements the minimum surface area required by the ABC
or Protocol, then exercises the interface. These tests serve as the executable
spec for how new sources / signals / strategies / adapters / scorers must look.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import polars as pl
import pytest
from numpy.typing import NDArray

from supertrader.data import DataSource, StoreWriter
from supertrader.execution import ExecutionAdapter, ExecutionReport
from supertrader.execution.base import Fill
from supertrader.signals import PointInTimeStore, Signal
from supertrader.signals.reddit_sentiment import SentimentScorer
from supertrader.strategies import Strategy

# ──────────────────────── DataSource / StoreWriter ────────────────────────


class _FakeStore:
    def __init__(self) -> None:
        self.written: list[tuple[str, int, tuple[str, ...]]] = []

    def write(self, source_id: str, frame: pl.LazyFrame, *, partition_keys: tuple[str, ...]) -> int:
        rows = frame.collect().height
        self.written.append((source_id, rows, partition_keys))
        return rows


class _FakeSource:
    source_id: str = "fake.prices"
    output_schema: pl.Schema = pl.Schema({"ticker": pl.Utf8, "date": pl.Date, "close": pl.Float64})

    def fetch(self, start: date, end: date, universe: list[str]) -> pl.LazyFrame:
        rows = [{"ticker": t, "date": start, "close": 100.0 + i} for i, t in enumerate(universe)]
        return pl.LazyFrame(rows, schema=self.output_schema)

    def ingest(self, start: date, end: date, universe: list[str], store: StoreWriter) -> int:
        frame = self.fetch(start, end, universe)
        return store.write(self.source_id, frame, partition_keys=("ticker",))


class TestDataSourceProtocol:
    def test_fake_source_satisfies_protocol(self) -> None:
        source = _FakeSource()
        assert isinstance(source, DataSource)

    def test_store_writer_protocol_runtime_check(self) -> None:
        store = _FakeStore()
        assert isinstance(store, StoreWriter)

    def test_ingest_writes_to_store(self) -> None:
        source = _FakeSource()
        store = _FakeStore()
        rows = source.ingest(date(2024, 1, 1), date(2024, 1, 2), ["AAPL", "MSFT"], store)
        assert rows == 2
        assert store.written == [("fake.prices", 2, ("ticker",))]


# ──────────────────────────── Signal ────────────────────────────


class _FakePITStore:
    def __init__(self, as_of: date) -> None:
        self.as_of = as_of

    def scan(self, source_id: str) -> pl.LazyFrame:
        return pl.LazyFrame({"date": [self.as_of], "ticker": ["AAPL"], "value": [1.0]})


class _ConstSignal(Signal):
    signal_id = "const"
    required_sources = ("fake.prices",)

    def __init__(self, value: float) -> None:
        self.value = value

    def compute(
        self,
        store: PointInTimeStore,
        start: date,
        end: date,
        universe: list[str],
    ) -> pd.DataFrame:
        idx = pd.date_range(start, end, freq="D", tz="UTC")
        return pd.DataFrame(self.value, index=idx, columns=universe, dtype="float64")

    def _fingerprint_parts(self) -> tuple[object, ...]:
        return (self.value,)


class TestSignal:
    def test_pit_store_protocol_satisfied(self) -> None:
        store = _FakePITStore(date(2024, 1, 1))
        assert isinstance(store, PointInTimeStore)

    def test_compute_returns_expected_shape(self) -> None:
        sig = _ConstSignal(value=0.5)
        store = _FakePITStore(date(2024, 1, 5))
        out = sig.compute(store, date(2024, 1, 1), date(2024, 1, 5), ["AAPL", "MSFT"])
        assert out.shape == (5, 2)
        assert (out.values == 0.5).all()

    def test_fingerprint_is_deterministic(self) -> None:
        a = _ConstSignal(value=0.5).fingerprint()
        b = _ConstSignal(value=0.5).fingerprint()
        assert a == b

    def test_fingerprint_changes_with_config(self) -> None:
        a = _ConstSignal(value=0.5).fingerprint()
        b = _ConstSignal(value=0.6).fingerprint()
        assert a != b

    def test_fingerprint_is_hex(self) -> None:
        fp = _ConstSignal(value=0.5).fingerprint()
        int(fp, 16)  # must parse


# ──────────────────────────── Strategy ────────────────────────────


class _EqualWeightStrategy(Strategy):
    strategy_id = "equal_weight"
    required_signals = ("const",)

    def target_positions(
        self,
        signals: dict[str, pd.DataFrame],
        prices: pd.DataFrame,
    ) -> pd.DataFrame:
        sig = signals[self.required_signals[0]]
        return sig.div(sig.abs().sum(axis=1), axis=0).fillna(0.0)


class TestStrategy:
    def test_target_positions_shape(self) -> None:
        strat = _EqualWeightStrategy()
        idx = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
        sig = pd.DataFrame(1.0, index=idx, columns=["AAPL", "MSFT", "NVDA"])
        prices = sig * 100.0
        weights = strat.target_positions({"const": sig}, prices)
        assert weights.shape == sig.shape
        np.testing.assert_allclose(weights.sum(axis=1), 1.0)


# ──────────────────────────── ExecutionAdapter ────────────────────────────


class _NoopAdapter(ExecutionAdapter):
    adapter_id = "noop"
    is_live = False

    def __init__(self) -> None:
        self._positions = pd.Series(dtype="float64")

    def reconcile_positions(self) -> pd.Series:
        return self._positions.copy()

    def execute(self, target_positions: pd.Series, as_of: datetime) -> ExecutionReport:
        return ExecutionReport(
            as_of=as_of,
            fills=[],
            rejected=[],
            pending=list(target_positions.index),
        )


class TestExecutionAdapter:
    def test_noop_adapter_reports_pending(self) -> None:
        adapter = _NoopAdapter()
        targets = pd.Series({"AAPL": 0.5, "MSFT": -0.3})
        report = adapter.execute(targets, datetime(2024, 1, 1, 16, 0))
        assert report.fills == []
        assert sorted(report.pending) == ["AAPL", "MSFT"]
        assert report.realized_pnl == Decimal(0)

    def test_fill_dataclass_frozen(self) -> None:
        f = Fill(
            ticker="AAPL",
            side="buy",
            quantity=Decimal(10),
            price=Decimal("150.50"),
            timestamp=datetime(2024, 1, 1),
        )
        with pytest.raises(Exception):  # noqa: B017, PT011  # frozen dataclass raises FrozenInstanceError
            f.ticker = "MSFT"  # type: ignore[misc]


# ──────────────────────────── SentimentScorer ────────────────────────────


class _FakeScorer(SentimentScorer):
    scorer_id = "fake"
    model_version = "v0"

    def score(self, texts: list[str]) -> NDArray[np.float64]:
        return np.array([len(t) / 100.0 for t in texts], dtype=np.float64).clip(-1, 1)


class TestSentimentScorer:
    def test_score_shape_and_dtype(self) -> None:
        scorer = _FakeScorer()
        out = scorer.score(["hello", "this is a longer string", ""])
        assert out.shape == (3,)
        assert out.dtype == np.float64
        assert ((out >= -1) & (out <= 1)).all()

    def test_empty_batch(self) -> None:
        scorer = _FakeScorer()
        out = scorer.score([])
        assert out.shape == (0,)
