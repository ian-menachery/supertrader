"""Top-level Pydantic config models. Every run is fully specified by a `RunConfig`."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    @field_validator("snapshot_path", mode="before")
    @classmethod
    def _coerce_snapshot_path(cls, v: object) -> object:
        """Allow string paths in YAML — strict mode otherwise rejects str→Path."""
        if isinstance(v, str):
            return Path(v)
        return v


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
    """Transaction-cost model parameters. See backtest/costs.py for application.

    Per ADR 0010 (cost-model v2) the platform supports two model versions:

      * **v1** — flat slippage at `slippage_bps_base` per side. Original
        rsm_v1 + v2-tech runs used this. Kept for historical reproducibility.
      * **v2** — flat half-spread at `half_spread_bps` per side (default 5
        for liquid names). Strictly more conservative than v1 at default
        values. The impact-term refinement (`slippage_impact_coeff_bps *
        sqrt(order_notional / ADV)`) is reserved for a future v2.1 once
        volume / ADV data flows through the engine.

    New configs default to `model_version: "v2"`. Existing
    `rsm_v1_backtest*.yaml` and `v2_tech_*.yaml` explicitly pin
    `model_version: "v1"` so their historical metrics stay
    bit-for-bit reproducible.
    """

    commission_bps: float = 1.0
    # v1 cost model (kept for historical reproducibility)
    slippage_bps_base: float = 3.0
    slippage_impact_coeff_bps: float = 10.0
    # v2 cost model
    model_version: Literal["v1", "v2"] = "v2"
    half_spread_bps: float = 5.0
    # Shared
    borrow_bps_annual: float = 50.0
    hard_to_borrow_bps_annual: float = 500.0
    htb_overrides_path: Path | None = None


class BacktestConfig(StrictModel):
    """Backtest run parameters including the train/test/holdout split.

    The split is enforced by `backtest.splits.HoldoutGuard`. Any run that touches
    the holdout window for a given config hash is recorded exactly once.

    Per ADR 0011, the four-date schema is flexible enough for any window length
    (PEAD wants 5y train + 1y test + 1y holdout; smoke configs want a few days
    each). The validators below codify the contract:

      * start <= train_end < test_end <= end
      * test_end == end is only allowed when allow_empty_holdout=True (smoke
        configs opt in explicitly so a missing holdout is never accidental).
      * Each non-empty window spans at least `MIN_WINDOW_CALENDAR_DAYS` days
        so degenerate metrics don't slip through unnoticed.
    """

    MIN_WINDOW_CALENDAR_DAYS: ClassVar[int] = 5

    start: date
    end: date
    initial_capital: float = 1_000_000.0
    rebalance_frequency: str = "1d"
    train_end: date
    test_end: date
    allow_empty_holdout: bool = Field(
        default=False,
        description=(
            "Opt-in flag for configs that intentionally skip the holdout window "
            "(typically smoke tests). When False, test_end must be strictly before end."
        ),
    )
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
        if self.test_end == self.end and not self.allow_empty_holdout:
            msg = (
                "test_end == end implies an empty holdout window. "
                "If intentional (smoke test), set allow_empty_holdout=true."
            )
            raise ValueError(msg)
        min_days = self.MIN_WINDOW_CALENDAR_DAYS
        if (self.train_end - self.start).days < min_days:
            msg = f"Train window must span at least {min_days} calendar days."
            raise ValueError(msg)
        if (self.test_end - self.train_end).days < min_days:
            msg = f"Test window must span at least {min_days} calendar days."
            raise ValueError(msg)
        if not self.allow_empty_holdout and (self.end - self.test_end).days < min_days:
            msg = f"Holdout window must span at least {min_days} calendar days."
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
