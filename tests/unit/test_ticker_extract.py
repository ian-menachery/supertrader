"""Tests for the ticker extractor — cashtag, bareword, blocklist, edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from supertrader.signals.reddit_sentiment.ticker_extract import (
    extract_tickers,
    load_blocklist,
)

UNIVERSE: set[str] = {
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "GME",
    "AMC",
    "PLTR",
    "GOOG",
    "GOOGL",
    "F",
    "V",
    "AMD",
    "MY",  # MY is in blocklist
    "IT",
    "GO",
    "DD",  # all in blocklist
}

DEFAULT_BLOCKLIST: set[str] = {"MY", "IT", "GO", "DD", "ALL", "FOR", "ON", "NEW"}


class TestCashtag:
    def test_simple_cashtag(self) -> None:
        assert extract_tickers("$TSLA puts", UNIVERSE) == {"TSLA"}

    def test_multiple_cashtags(self) -> None:
        out = extract_tickers("Loaded $TSLA and $GME calls", UNIVERSE)
        assert out == {"TSLA", "GME"}

    def test_off_universe_cashtag_dropped(self) -> None:
        # $XYZ is not in the universe — must drop even though syntax matches.
        assert extract_tickers("$XYZ moonshot", UNIVERSE) == set()

    def test_cashtag_ignores_bareword_blocklist(self) -> None:
        # Cashtags bypass the blocklist; $MY is an explicit ticker reference.
        out = extract_tickers("$MY position", UNIVERSE, blocklist=DEFAULT_BLOCKLIST)
        assert out == {"MY"}

    def test_one_char_ticker_via_cashtag(self) -> None:
        # 1-char ticker $F (Ford) recovered through cashtag form.
        assert extract_tickers("$F is a value play", UNIVERSE) == {"F"}


class TestBareword:
    def test_bareword_in_universe(self) -> None:
        assert extract_tickers("thinking about TSLA", UNIVERSE) == {"TSLA"}

    def test_bareword_off_universe_dropped(self) -> None:
        assert extract_tickers("loving ABCDE today", UNIVERSE) == set()

    def test_bareword_blocked_word_dropped(self) -> None:
        # MY is in the universe AND in the blocklist; blocklist wins for barewords.
        out = extract_tickers("I love MY car", UNIVERSE, blocklist=DEFAULT_BLOCKLIST)
        assert out == set()

    def test_bareword_lowercase_ignored(self) -> None:
        # Regex requires uppercase; lowercase ticker mentions are not extracted.
        assert extract_tickers("thinking about tsla", UNIVERSE) == set()

    def test_bareword_mixed_case(self) -> None:
        # "AAPL aapl" — uppercase form matches, lowercase doesn't.
        assert extract_tickers("AAPL aapl", UNIVERSE) == {"AAPL"}

    def test_googl_vs_goog(self) -> None:
        out = extract_tickers("GOOGL vs GOOG", UNIVERSE)
        assert out == {"GOOGL", "GOOG"}

    def test_one_char_bareword_not_extracted(self) -> None:
        # 1-char barewords would explode false positives ("A", "I"). Pattern
        # requires length 2+.
        assert extract_tickers("F is great but I prefer V", UNIVERSE) == set()


class TestBlocklistInteraction:
    def test_blocklist_suppresses_real_ticker(self) -> None:
        # IT is a real ticker (Gartner) but as a bareword it's noise.
        out = extract_tickers("the new IT department", UNIVERSE, blocklist=DEFAULT_BLOCKLIST)
        assert out == set()

    def test_blocklist_suppresses_go(self) -> None:
        out = extract_tickers("GO read the prospectus", UNIVERSE, blocklist=DEFAULT_BLOCKLIST)
        assert out == set()

    def test_default_no_blocklist_allows_through(self) -> None:
        # Without blocklist, MY-as-bareword matches.
        assert extract_tickers("I love MY car", UNIVERSE) == {"MY"}


class TestAllowBareword:
    def test_disabled_only_cashtag(self) -> None:
        out = extract_tickers(
            "TSLA and $GME and bareword AAPL",
            UNIVERSE,
            allow_bareword=False,
        )
        assert out == {"GME"}


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert extract_tickers("", UNIVERSE) == set()

    def test_no_tickers(self) -> None:
        assert extract_tickers("just a normal sentence", UNIVERSE) == set()

    def test_punctuation_around_cashtag(self) -> None:
        out = extract_tickers("got into ($TSLA, $GME): big positions.", UNIVERSE)
        assert out == {"TSLA", "GME"}

    def test_cashtag_max_length_strict(self) -> None:
        # The \b word-boundary anchor enforces that the cashtag terminates after
        # 1-to-5 capitals followed by a non-word char (space, punctuation, EOL).
        # `$GOOGLE` has 6 capitals followed by " ", so no prefix-of-5 satisfies
        # the boundary. Strict behavior — safer than greedy substring matching.
        assert extract_tickers("$GOOGLE is not a ticker", UNIVERSE) == set()

    def test_cashtag_five_chars_with_boundary(self) -> None:
        # $GOOGL followed by space → matches GOOGL cleanly.
        assert extract_tickers("$GOOGL is the C-share", UNIVERSE) == {"GOOGL"}


class TestBlocklistLoading:
    def test_load_repo_blocklist(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "configs" / "ticker_blocklist.yaml"
        blocklist = load_blocklist(path)
        assert "MY" in blocklist
        assert "IT" in blocklist
        assert "GO" in blocklist
        assert len(blocklist) >= 30

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_blocklist(tmp_path / "nope.yaml")

    def test_invalid_yaml_shape_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("tickers: not_a_list\n", encoding="utf-8")
        with pytest.raises(TypeError, match="must be a list"):
            load_blocklist(path)
