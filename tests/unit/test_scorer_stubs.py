"""Tests for FinBert and LLM scorer stubs — registry presence + NotImplementedError."""

from __future__ import annotations

import pytest

from supertrader.config.registry import scorers
from supertrader.signals.reddit_sentiment.scorer_finbert import FinBertScorer
from supertrader.signals.reddit_sentiment.scorer_llm import LLMScorer


class TestRegistryPresence:
    def test_finbert_registered(self) -> None:
        assert "finbert" in scorers
        assert scorers.resolve("finbert") is FinBertScorer

    def test_llm_registered(self) -> None:
        assert "llm" in scorers
        assert scorers.resolve("llm") is LLMScorer


class TestNotImplementedRaises:
    def test_finbert_raises_with_adr_reference(self) -> None:
        scorer = FinBertScorer()
        with pytest.raises(NotImplementedError, match="ADR 0006"):
            scorer.score(["any text"])

    def test_llm_raises_with_adr_reference(self) -> None:
        scorer = LLMScorer()
        with pytest.raises(NotImplementedError, match="ADR 0006"):
            scorer.score(["any text"])


class TestModelVersion:
    def test_finbert_has_stub_version(self) -> None:
        assert FinBertScorer().model_version == "finbert-stub-0"

    def test_llm_has_stub_version(self) -> None:
        assert LLMScorer().model_version == "llm-stub-0"
