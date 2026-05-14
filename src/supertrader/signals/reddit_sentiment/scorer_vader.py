"""VADER-based sentiment scorer with a finance-flavoured lexicon overlay.

`vaderSentiment` ships a general-purpose social-media lexicon. WSB-style
finance text uses terms like `puts`, `bagholder`, `to the moon` that VADER
either gets wrong or ignores. We override those weights on construction via
`analyzer.lexicon.update(...)`.

The lexicon's file-level `version:` string is folded into `model_version` so
the signal cache invalidates automatically when terms are edited.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml
from numpy.typing import NDArray
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from supertrader.config.registry import scorers
from supertrader.signals.reddit_sentiment.scorer_base import SentimentScorer

DEFAULT_LEXICON_PATH = Path("configs") / "sentiment_lexicon.yaml"


def _load_lexicon(path: Path) -> tuple[dict[str, float], str]:
    """Return (term -> weight, version) from the YAML lexicon."""
    if not path.exists():
        msg = f"Sentiment lexicon not found at {path}"
        raise FileNotFoundError(msg)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    version = str(data.pop("version", "no-version"))
    weights: dict[str, float] = {}
    for term, weight in data.items():
        if not isinstance(term, str):
            continue
        weights[term.lower()] = float(weight)
    return weights, version


@scorers.register("vader")
class VaderScorer(SentimentScorer):
    """Wraps `SentimentIntensityAnalyzer` with the finance lexicon overlay.

    The compound score (range [-1, 1]) is what we surface — it normalizes
    positive and negative components by length.
    """

    scorer_id: str = "vader"

    def __init__(self, lexicon_path: Path | str | None = None) -> None:
        path = Path(lexicon_path) if lexicon_path else DEFAULT_LEXICON_PATH
        weights, version = _load_lexicon(path)
        self._analyzer = SentimentIntensityAnalyzer()
        # VADER's lexicon is a dict[str, float]; update merges our overrides in.
        self._analyzer.lexicon.update(weights)
        self._lexicon_version = version
        self.model_version: str = f"vader-1.0+finlex-{version}"

    def score(self, texts: list[str]) -> NDArray[np.float64]:
        if not texts:
            return np.zeros(0, dtype=np.float64)
        out = np.empty(len(texts), dtype=np.float64)
        for i, text in enumerate(texts):
            out[i] = self._analyzer.polarity_scores(text)["compound"]
        return out
