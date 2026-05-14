"""FinBERT sentiment scorer — Phase 2 stub.

ADR 0006 records the upgrade trigger: replace `VaderScorer` with `FinBertScorer`
when VADER + finance lexicon AUC drops below 0.55 on the 500-post hand-labeled
holdout set at `tests/golden/sentiment_eval_500.csv`.

The class is wired through the `scorers` registry so the swap is a config
change (`scorer.type: finbert`), not code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from supertrader.config.registry import scorers
from supertrader.signals.reddit_sentiment.scorer_base import SentimentScorer

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@scorers.register("finbert")
class FinBertScorer(SentimentScorer):
    """FinBERT-based scorer. Not yet implemented — see ADR 0006."""

    scorer_id: str = "finbert"
    model_version: str = "finbert-stub-0"

    def score(self, texts: list[str]) -> NDArray[np.float64]:
        msg = (
            "FinBertScorer is a Phase 2 stub. See ADR 0006 for the upgrade "
            "trigger (VADER AUC < 0.55 on the 500-post hand-labeled holdout). "
            "Implementation requires the `transformers` and `torch` packages "
            "plus a pre-trained FinBERT checkpoint."
        )
        raise NotImplementedError(msg)
