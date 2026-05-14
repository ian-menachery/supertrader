"""ExecutionAdapter ABC. Target weights → orders. Backtest vs. paper vs. live."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import pandas as pd


OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["filled", "partial", "rejected", "pending"]


@dataclass(frozen=True, slots=True)
class Fill:
    """A single (partial) order fill."""

    ticker: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    commission: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Result of one `execute` call: which orders filled, which didn't, realized PnL.

    Adapters must populate this regardless of backtest vs. live. Tests assert
    against its shape, not against engine internals.
    """

    as_of: datetime
    fills: list[Fill] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(
        default_factory=list, metadata={"doc": "(ticker, reason)"}
    )
    pending: list[str] = field(default_factory=list)
    realized_pnl: Decimal = Decimal(0)


class ExecutionAdapter(ABC):
    """Translates target weights into orders against a venue (real or simulated).

    Contract:
      * `reconcile_positions` returns current state (signed shares per ticker).
      * `execute(target_positions, as_of)` submits orders to move from current
        toward target. Returns an `ExecutionReport`.
      * Adapters never compute strategy logic. They take weights and route them.
      * Backtest adapter is dry-run; paper/live are wet.
    """

    adapter_id: str
    is_live: bool

    @abstractmethod
    def reconcile_positions(self) -> pd.Series:
        """Return current positions: index = ticker, value = signed shares."""

    @abstractmethod
    def execute(
        self,
        target_positions: pd.Series,
        as_of: datetime,
    ) -> ExecutionReport:
        """Submit orders to reach `target_positions`. Returns the resulting report."""
