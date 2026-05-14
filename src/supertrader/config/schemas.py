"""Top-level Pydantic config models. Every run is fully specified by a `RunConfig`."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base class for all config models.

    Frozen, forbids unknown fields, validates on assignment, strict type coercion.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )


class DataSourceConfig(StrictModel):
    """Declarative reference to a concrete DataSource implementation.

    `type` is a registry key resolved at runtime to a `DataSource` subclass.
    `params` is forwarded to that subclass's own Pydantic model for validation.
    """

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class UniverseConfig(StrictModel):
    """How the tradeable universe is defined for this run."""

    type: str = Field(description="'static' | 'russell_1000_snapshot' | 'pit'")
    snapshot_path: Path | None = None
    max_market_cap_usd: float | None = None
    min_market_cap_usd: float | None = None
    min_adv_usd: float | None = Field(
        default=None, description="Average daily dollar volume floor in USD."
    )
    exclude_tickers: list[str] = Field(default_factory=list)


class SignalConfig(StrictModel):
    """Declarative reference to a Signal implementation."""

    type: str
    name: str = Field(description="Unique signal id at runtime, used as cache key.")
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class StrategyConfig(StrictModel):
    """Declarative reference to a Strategy implementation and its inputs."""

    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    signals: list[str] = Field(description="Signal names this strategy consumes.")


class CostsConfig(StrictModel):
    """Transaction-cost model parameters. See backtest/costs.py for application."""

    commission_bps: float = 1.0
    slippage_bps_base: float = 3.0
    slippage_impact_coeff_bps: float = 10.0
    borrow_bps_annual: float = 50.0
    hard_to_borrow_bps_annual: float = 500.0
    htb_overrides_path: Path | None = None


class BacktestConfig(StrictModel):
    """Backtest run parameters including the train/test/holdout split.

    The split is enforced by `backtest.splits.HoldoutGuard`. Any run that touches
    the holdout window for a given config hash is recorded exactly once.
    """

    start: date
    end: date
    initial_capital: float = 1_000_000.0
    rebalance_frequency: str = "1d"
    train_end: date
    test_end: date
    execution_delay_bars: int = Field(
        default=1, description="Bars between signal-as-of and order-fill. 1 = next-day open."
    )
    costs: CostsConfig = Field(default_factory=CostsConfig)

    @model_validator(mode="after")
    def _check_split_ordering(self) -> BacktestConfig:
        if not (self.start <= self.train_end < self.test_end <= self.end):
            msg = (
                "Backtest dates must satisfy: start <= train_end < test_end <= end. "
                f"Got start={self.start}, train_end={self.train_end}, "
                f"test_end={self.test_end}, end={self.end}."
            )
            raise ValueError(msg)
        return self


class ExecutionConfig(StrictModel):
    """Declarative reference to an ExecutionAdapter implementation."""

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class RunConfig(StrictModel):
    """The top-level config. One YAML file → one `RunConfig` → one reproducible run."""

    run_id: str = Field(min_length=1, max_length=128)
    extends: Path | None = Field(
        default=None, description="Optional parent YAML; merged before this file is applied."
    )
    universe: UniverseConfig
    data_sources: list[DataSourceConfig]
    signals: list[SignalConfig]
    strategy: StrategyConfig
    backtest: BacktestConfig
    execution: ExecutionConfig

    @model_validator(mode="after")
    def _check_strategy_signals_declared(self) -> RunConfig:
        declared_names = {s.name for s in self.signals}
        missing = [s for s in self.strategy.signals if s not in declared_names]
        if missing:
            msg = (
                f"Strategy references signals not declared in run: {missing}. "
                f"Declared signals: {sorted(declared_names)}."
            )
            raise ValueError(msg)
        return self
