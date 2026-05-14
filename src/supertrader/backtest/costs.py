"""Commission cost model.

Round-trip commission is charged per-side at a flat bps rate of the order
notional. The rate comes from `CostsConfig.commission_bps` and stays constant
within a run.

Per-tier commission tiers (e.g., 0.5/1.0/2.0 bps by ADV tier) are a Phase 2
enhancement — for v1 we use a single configurable rate, with the assumption
that the universe is filtered to liquid names where 1 bps is realistic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supertrader.config.schemas import CostsConfig


def commission_fraction(costs: CostsConfig) -> float:
    """Commission as a fraction of trade notional (bps / 10000)."""
    return float(costs.commission_bps) / 10_000.0


def commission_dollars(notional: float, costs: CostsConfig) -> float:
    """Dollar commission charged on a trade of `notional` dollars (one side)."""
    return abs(notional) * commission_fraction(costs)
