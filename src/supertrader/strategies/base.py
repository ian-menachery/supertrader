"""Strategy ABC. Pure function: signals + prices → target weights."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


class Strategy(ABC):
    """Consumes one or more signals, emits target portfolio weights.

    Contract:
      * Input: `signals: dict[signal_name, DataFrame]`. Each DataFrame indexed by
        date with tickers as columns. `prices`: same shape, close prices.
      * Output: DataFrame same shape as inputs, values are target weights in
        `[-1, 1]`. Negative = short. Weights need not sum to 1; the execution
        layer rescales according to gross/net limits.
      * Strategies are pure: same inputs → same outputs. No I/O.
      * Strategies do not know about costs, slippage, or order routing.
    """

    strategy_id: str
    required_signals: tuple[str, ...]

    @abstractmethod
    def target_positions(
        self,
        signals: dict[str, pd.DataFrame],
        prices: pd.DataFrame,
    ) -> pd.DataFrame:
        """Return target weights, indexed by date, columns are tickers."""
