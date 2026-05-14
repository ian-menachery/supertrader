"""Position-sizing and risk-overlay helpers.

v1 is intentionally minimal: only `scale_to_gross` is supported. Sector caps,
position-size caps, and per-ticker exposure limits are deferred to Phase 2 —
the universe is small enough for v1 that crude gross-exposure scaling is
adequate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def scale_to_gross(weights: pd.DataFrame, target_gross: float = 1.0) -> pd.DataFrame:
    """Rescale each row so `sum(|w|)` equals `target_gross`.

    Rows that are entirely zero (or NaN) are left untouched.
    """
    if weights.empty:
        return weights.copy()
    w = weights.fillna(0.0).astype("float64")
    row_gross = w.abs().sum(axis=1)
    # Replace 0 with 1 to avoid div-by-zero; rows with sum 0 stay all-zero.
    safe = row_gross.where(row_gross > 0, 1.0)
    scaled = w.div(safe, axis=0) * target_gross
    # Restore exact zeros for empty rows
    empty_rows = row_gross == 0
    if empty_rows.any():
        scaled.loc[empty_rows, :] = 0.0
    return scaled
