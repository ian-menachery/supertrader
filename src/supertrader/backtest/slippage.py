"""Slippage model: textbook square-root market impact.

    slippage_bps = base + impact_coeff * sqrt(notional / adv_dollar)

The base captures the bid-ask half-spread; the second term grows with order
size relative to liquidity. `adv_dollar` is the 20-day average dollar volume
for the ticker — loaded from the universe snapshot at strategy boundary.

Tickers with unknown ADV (or zero) are treated as worst-case-liquid: full
impact_coeff is applied (equivalent to ordering 100% of ADV).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supertrader.config.schemas import CostsConfig


def slippage_bps(notional: float, adv_dollar: float, costs: CostsConfig) -> float:
    """Slippage in basis points for a trade of `notional` against `adv_dollar`."""
    base = float(costs.slippage_bps_base)
    impact = float(costs.slippage_impact_coeff_bps)
    if adv_dollar <= 0:
        # Worst-case: order is 100% of ADV.
        return base + impact
    ratio = abs(notional) / adv_dollar
    return base + impact * math.sqrt(ratio)


def slippage_fraction(notional: float, adv_dollar: float, costs: CostsConfig) -> float:
    """Slippage as a fraction of trade notional."""
    return slippage_bps(notional, adv_dollar, costs) / 10_000.0


def slippage_dollars(notional: float, adv_dollar: float, costs: CostsConfig) -> float:
    """Dollar slippage charged on a trade of `notional` against `adv_dollar`."""
    return abs(notional) * slippage_fraction(notional, adv_dollar, costs)
