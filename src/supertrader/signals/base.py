"""Signal ABC + the PointInTimeStore wrapper that prevents lookahead bias."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl


@runtime_checkable
class PointInTimeStore(Protocol):
    """A store view that refuses to return data with timestamps later than `as_of`.

    `Signal.compute` receives an instance of this rather than the raw store. The
    backtest engine iterates signals over time and passes a PIT view at each step.
    Production implementation lives in `data.store`.
    """

    as_of: date

    def scan(self, source_id: str) -> pl.LazyFrame:
        """Scan data for a source, filtered to rows with as-of timestamp ≤ self.as_of."""
        ...


class Signal(ABC):
    """A function of stored data producing a wide (date-by-ticker -> float) panel.

    Contract:
      * Inputs come from a `PointInTimeStore` only — never a raw source.
      * Output is a pandas DataFrame, `DatetimeIndex` in UTC, columns are tickers,
        values are float64 (NaN allowed for missing).
      * `required_sources` declares data dependencies (used for cache invalidation).
      * `compute` is deterministic: same store state + same config → same output.
      * Implementations must never read data with timestamp > the per-row index.
        The PIT store enforces this for cross-source reads; intra-frame lookahead
        is the implementation's responsibility.
    """

    signal_id: str
    required_sources: tuple[str, ...]

    @abstractmethod
    def compute(
        self,
        store: PointInTimeStore,
        start: date,
        end: date,
        universe: list[str],
    ) -> pd.DataFrame:
        """Produce signal values for the window and universe."""

    def fingerprint(self) -> str:
        """Stable hash of (signal_id, config, code version). Used as cache key.

        Subclasses should override `_fingerprint_parts` to contribute their own
        config to the hash. The default hashes only `signal_id` and the tuple of
        required sources — insufficient for any real signal, deliberately.
        """
        parts = (self.signal_id, sorted(self.required_sources), *self._fingerprint_parts())
        payload = json.dumps(parts, sort_keys=True, default=str).encode()
        return hashlib.blake2b(payload, digest_size=16).hexdigest()

    def _fingerprint_parts(self) -> tuple[object, ...]:
        """Subclass hook: extra config to include in the fingerprint."""
        return ()
