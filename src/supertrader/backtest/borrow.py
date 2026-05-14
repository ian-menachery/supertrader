"""Stock-borrow cost model for short positions.

vectorbt (free version) does not model borrow. We apply borrow cost as a
post-process deduction from the daily return series: each day a short is
held, deduct `(annual_rate / 365) * |short_notional|`.

v1 assumes all shorts are easy-to-borrow at `costs.borrow_bps_annual`. A
`htb_overrides.yaml` per-ticker hard-to-borrow override is Phase 2 and
referenced via `costs.htb_overrides_path` but not yet honored. Loud comment
in the code; commit notes flag the limitation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supertrader.config.schemas import CostsConfig


DAYS_PER_YEAR = 365.0


def annual_rate(costs: CostsConfig, *, hard_to_borrow: bool = False) -> float:
    """Annualized borrow rate as a fraction (bps / 10000)."""
    bps = float(costs.hard_to_borrow_bps_annual if hard_to_borrow else costs.borrow_bps_annual)
    return bps / 10_000.0


def daily_rate(costs: CostsConfig, *, hard_to_borrow: bool = False) -> float:
    """Per-day borrow rate as a fraction."""
    return annual_rate(costs, hard_to_borrow=hard_to_borrow) / DAYS_PER_YEAR


def borrow_dollars(
    short_notional: float,
    days_held: int,
    costs: CostsConfig,
    *,
    hard_to_borrow: bool = False,
) -> float:
    """Dollar borrow cost for holding `short_notional` short for `days_held` calendar days."""
    if short_notional >= 0 or days_held <= 0:
        return 0.0
    rate = daily_rate(costs, hard_to_borrow=hard_to_borrow)
    return abs(short_notional) * rate * float(days_held)
