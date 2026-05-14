"""Config schema validation tests. These are the contract for every future run config."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from supertrader.config import (
    BacktestConfig,
    CostsConfig,
    DataSourceConfig,
    ExecutionConfig,
    RunConfig,
    SignalConfig,
    StrategyConfig,
    UniverseConfig,
)


def _valid_run_kwargs() -> dict[str, object]:
    return {
        "run_id": "test-run-0001",
        "universe": UniverseConfig(type="static"),
        "data_sources": [DataSourceConfig(type="yfinance.prices.daily")],
        "signals": [SignalConfig(type="reddit_sentiment", name="sent_v1")],
        "strategy": StrategyConfig(type="mean_reversion", signals=["sent_v1"]),
        "backtest": BacktestConfig(
            start=date(2020, 1, 1),
            end=date(2024, 12, 31),
            train_end=date(2022, 12, 31),
            test_end=date(2023, 12, 31),
            costs=CostsConfig(),
        ),
        "execution": ExecutionConfig(type="backtest"),
    }


class TestStrictness:
    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UniverseConfig(type="static", bogus_field="x")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        cfg = UniverseConfig(type="static")
        with pytest.raises(ValidationError):
            cfg.type = "russell_1000_snapshot"  # type: ignore[misc]


class TestBacktestConfigSplit:
    def test_valid_split(self) -> None:
        cfg = BacktestConfig(
            start=date(2020, 1, 1),
            end=date(2024, 12, 31),
            train_end=date(2022, 12, 31),
            test_end=date(2023, 12, 31),
        )
        assert cfg.train_end < cfg.test_end < cfg.end

    @pytest.mark.parametrize(
        ("start", "train_end", "test_end", "end"),
        [
            # train_end >= test_end
            (date(2020, 1, 1), date(2023, 12, 31), date(2023, 12, 31), date(2024, 12, 31)),
            # test_end > end
            (date(2020, 1, 1), date(2022, 12, 31), date(2025, 1, 1), date(2024, 12, 31)),
            # start > train_end
            (date(2023, 6, 1), date(2022, 12, 31), date(2023, 12, 31), date(2024, 12, 31)),
        ],
    )
    def test_invalid_splits_raise(
        self, start: date, train_end: date, test_end: date, end: date
    ) -> None:
        with pytest.raises(ValidationError):
            BacktestConfig(start=start, end=end, train_end=train_end, test_end=test_end)


class TestRunConfigCrossValidation:
    def test_strategy_signal_must_be_declared(self) -> None:
        kwargs = _valid_run_kwargs()
        kwargs["strategy"] = StrategyConfig(type="mean_reversion", signals=["does_not_exist"])
        with pytest.raises(ValidationError, match="not declared"):
            RunConfig(**kwargs)  # type: ignore[arg-type]

    def test_valid_run_config_builds(self) -> None:
        cfg = RunConfig(**_valid_run_kwargs())  # type: ignore[arg-type]
        assert cfg.run_id == "test-run-0001"
        assert cfg.strategy.signals == ["sent_v1"]
