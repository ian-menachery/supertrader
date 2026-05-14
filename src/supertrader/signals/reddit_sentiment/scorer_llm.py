"""LLM-based sentiment scorer with on-disk cache — Phase 2 stub.

ADR 0006 records the upgrade path: when both `VaderScorer` and `FinBertScorer`
have been evaluated and the gap to a cached-LLM baseline is large enough to
justify the cost, replace with this class. Cost-per-backfill is in the
$100-500 range for 10M posts via Haiku-class models.

Cache key is `(model_version, sha256(text))`. Cache hits are free; misses pay
the API tariff. Cache lives at `data/cache/llm_scorer/`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from supertrader.config.registry import scorers
from supertrader.signals.reddit_sentiment.scorer_base import SentimentScorer

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@scorers.register("llm")
class LLMScorer(SentimentScorer):
    """LLM-based scorer with disk cache. Not yet implemented — see ADR 0006."""

    scorer_id: str = "llm"
    model_version: str = "llm-stub-0"

    def score(self, texts: list[str]) -> NDArray[np.float64]:
        msg = (
            "LLMScorer is a Phase 2 stub. See ADR 0006 for the upgrade trigger. "
            "Implementation requires an Anthropic API key, on-disk SHA256-keyed "
            "cache, and a structured-output prompt that returns sentiment in "
            "[-1, 1]."
        )
        raise NotImplementedError(msg)
