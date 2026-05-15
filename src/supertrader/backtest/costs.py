"""Transaction cost model — commission + slippage.

Two model versions per ADR 0010:

  * `model_version == "v1"` — flat slippage at `slippage_bps_base` per side.
    Historical default; kept for reproducibility of rsm_v1 + v2-tech runs.
  * `model_version == "v2"` — flat half-spread at `half_spread_bps` per side.
    Strictly more conservative at default values (5 bps vs 3 bps). The
    impact-term refinement is reserved for v2.1 once volume / ADV data
    flows through the engine; the current implementation treats v2 as a
    higher-fidelity flat rate.

Commission is a separate, additive cost on top of slippage and applies
identically under both model versions.
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


def flat_slippage_fraction(costs: CostsConfig) -> float:
    """Per-side slippage as a fraction of trade notional (engine-facing).

    Dispatches on `costs.model_version`:
      * v1: `slippage_bps_base / 10000` — the original flat rate.
      * v2: `half_spread_bps / 10000` — a stricter flat rate representing
        the average bid-ask half-spread on the configured universe (5 bps
        default for SP500 large-caps; bump to 20-30 bps for meme/small-cap
        universes per the user-configurable field).

    Both versions return a scalar slippage. The per-cell sqrt-impact path
    (in `backtest.slippage`) is reserved for v2.1 once ADV / volume data
    is plumbed through to the engine.

    Named `flat_slippage_fraction` (not `slippage_fraction`) to avoid
    colliding with `backtest.slippage.slippage_fraction` which takes a
    per-order notional.
    """
    if costs.model_version == "v1":
        return float(costs.slippage_bps_base) / 10_000.0
    # costs.model_version is Literal["v1", "v2"] so this branch is exhaustive.
    return float(costs.half_spread_bps) / 10_000.0
