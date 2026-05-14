"""SentimentScorer ABC. The pluggability hook for VADER → FinBERT → LLM."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


class SentimentScorer(ABC):
    """Scores a batch of texts → array of floats in [-1, 1].

    Contract:
      * `score` is pure: same texts + same `model_version` → same outputs.
      * `model_version` participates in signal fingerprints. Bumping it
        invalidates downstream caches.
      * Batching is the implementation's responsibility; callers pass a list.
      * Implementations may use on-disk caching (LLMScorer must); the cache key
        is `(model_version, sha256(text))`.
    """

    scorer_id: str
    model_version: str

    @abstractmethod
    def score(self, texts: list[str]) -> NDArray[np.float64]:
        """Return shape `(len(texts),)`, dtype float64, values in `[-1, 1]`."""
