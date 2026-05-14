"""Tests for VaderScorer + finance lexicon overlay."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from supertrader.config.registry import scorers
from supertrader.signals.reddit_sentiment.scorer_vader import VaderScorer


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lexicon_path(repo_root: Path) -> Path:
    return repo_root / "configs" / "sentiment_lexicon.yaml"


@pytest.fixture(scope="module")
def scorer(lexicon_path: Path) -> VaderScorer:
    return VaderScorer(lexicon_path)


# 20 hand-scored messages with expected directional sign.
# +1 = positive, -1 = negative, 0 = neutral.
HAND_LABELED_MESSAGES: list[tuple[str, int]] = [
    ("This is an amazing earnings beat!", 1),
    ("To the moon with diamond hands!", 1),
    ("Loading up on $TSLA calls before earnings", 1),
    ("Massive short squeeze incoming", 1),
    ("Bullish setup, breakout confirmed", 1),
    ("I'm buying the dip here", 1),
    ("Beat earnings handily, ripping in afterhours", 1),
    ("Undervalued at these levels, big upgrade", 1),
    ("Down bad, holding bags on $GME", -1),
    ("Earnings miss, this is a disaster", -1),
    ("Rug pull alert, dumping everything", -1),
    ("Bearish, big crash coming", -1),
    ("Overvalued garbage, shorting it", -1),
    ("Rekt on puts, paper hands", -1),
    ("Plunge continues, no support in sight", -1),
    ("Tanking after the downgrade", -1),
    ("The company reported financial results today", 0),
    ("Volume was average for the session", 0),
    ("They announced a new product line", 0),
    ("Quarterly report posted on the SEC website", 0),
]


class TestSignAccuracy:
    def test_hand_labeled_messages_accuracy(self, scorer: VaderScorer) -> None:
        texts = [msg for msg, _ in HAND_LABELED_MESSAGES]
        expected_signs = [sign for _, sign in HAND_LABELED_MESSAGES]
        scores = scorer.score(texts)

        # Map score to sign with a small dead-zone for "neutral" labels.
        def predicted_sign(score: float, expected: int) -> int:
            if expected == 0:
                # For neutral labels, accept anything in [-0.3, 0.3] as correct.
                return 0 if abs(score) < 0.3 else (1 if score > 0 else -1)
            return 1 if score > 0 else (-1 if score < 0 else 0)

        correct = sum(
            1
            for s, e in zip(scores, expected_signs, strict=True)
            if predicted_sign(s, e) == e
        )
        accuracy = correct / len(scores)
        # Must hit at least 80% sign accuracy across the 20 messages.
        assert accuracy >= 0.80, (
            f"Sign accuracy {accuracy:.1%} below 80% gate. Scores: "
            + ", ".join(
                f"{m[:30]}->{s:.2f}" for m, s in zip(texts, scores, strict=True)
            )
        )


class TestLexiconOverrides:
    def test_puts_scores_negative(self, scorer: VaderScorer) -> None:
        # Plain VADER would treat "puts" as neutral. Our lexicon makes it bearish.
        score = scorer.score(["loaded with puts"])[0]
        assert score < 0

    def test_to_the_moon_scores_positive(self, scorer: VaderScorer) -> None:
        score = scorer.score(["to the moon"])[0]
        assert score > 0

    def test_bagholder_scores_negative(self, scorer: VaderScorer) -> None:
        score = scorer.score(["bagholder"])[0]
        assert score < 0

    def test_diamond_hands_scores_positive(self, scorer: VaderScorer) -> None:
        score = scorer.score(["diamond hands"])[0]
        assert score > 0


class TestDeterminism:
    def test_same_input_same_output(self, scorer: VaderScorer) -> None:
        a = scorer.score(["Loaded up on $TSLA calls"])
        b = scorer.score(["Loaded up on $TSLA calls"])
        np.testing.assert_array_equal(a, b)

    def test_empty_batch(self, scorer: VaderScorer) -> None:
        out = scorer.score([])
        assert out.shape == (0,)
        assert out.dtype == np.float64

    def test_output_range(self, scorer: VaderScorer) -> None:
        texts = [m for m, _ in HAND_LABELED_MESSAGES]
        scores = scorer.score(texts)
        assert ((scores >= -1) & (scores <= 1)).all()


class TestModelVersion:
    def test_model_version_includes_lexicon_version(self, scorer: VaderScorer) -> None:
        # Lexicon version "2026.05.14" must surface in the model_version string.
        assert "2026.05.14" in scorer.model_version
        assert scorer.model_version.startswith("vader-")


class TestRegistry:
    def test_vader_is_registered(self) -> None:
        assert "vader" in scorers
        assert scorers.resolve("vader") is VaderScorer


class TestMissingLexicon:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="lexicon"):
            VaderScorer(tmp_path / "nope.yaml")
