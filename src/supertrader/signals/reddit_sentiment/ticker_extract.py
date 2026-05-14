r"""Extract universe-valid stock tickers from free-form Reddit text.

Two patterns:
  * **Cashtag** `\$[A-Z]{1,5}` — strong signal, the `$` prefix is the author's
    explicit "this is a ticker" mark. Still filtered against the universe so
    off-universe mentions (e.g., `$XYZ` for a non-tradeable name) drop out.
  * **Bareword** `\b[A-Z]{2,5}\b` — weaker signal. Must be in the universe
    AND not in `configs/ticker_blocklist.yaml` (which catches things like
    `MY`, `IT`, `GO` — both English words and real tickers).

Length floor of 2 for barewords is deliberate: 1-character barewords like
"I" or "A" would produce too many false positives. Real 1-char tickers
(F, V, T, X) are recovered via cashtag form.

This module is a pure function. All state — universe set, blocklist — is
passed in by the caller (typically `RedditSentimentSignal`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import yaml

CASHTAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$([A-Z]{1,5})\b")
BAREWORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b([A-Z]{2,5})\b")


def load_blocklist(path: Path | str) -> set[str]:
    """Load the bareword blocklist from a YAML file with a top-level `tickers:` list."""
    p = Path(path)
    if not p.exists():
        msg = f"Ticker blocklist not found at {p}"
        raise FileNotFoundError(msg)
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    tickers = data.get("tickers", [])
    if not isinstance(tickers, list):
        msg = f"Blocklist `tickers` must be a list, got {type(tickers).__name__}"
        raise TypeError(msg)
    return {str(t).upper() for t in tickers}


def extract_tickers(
    text: str,
    universe: set[str],
    *,
    blocklist: set[str] | None = None,
    allow_bareword: bool = True,
) -> set[str]:
    """Return the set of universe-valid tickers mentioned in `text`.

    Args:
        text: The post body / title / comment to scan. Empty input → empty result.
        universe: The set of tradeable tickers to filter against. Membership is
            mandatory — off-universe matches are dropped.
        blocklist: Bareword exclusions. Cashtags bypass this. Defaults to empty
            (no exclusions); pass the result of `load_blocklist()` to enable.
        allow_bareword: When `False`, only cashtag form is honored. Useful for
            extremely noisy text (e.g., scraped news headlines).

    """
    if not text:
        return set()
    block = blocklist or set()
    found: set[str] = set()

    for match in CASHTAG_PATTERN.findall(text):
        # Cashtags bypass the bareword blocklist — `$MY` is unambiguous.
        if match in universe:
            found.add(match)

    if allow_bareword:
        for match in BAREWORD_PATTERN.findall(text):
            if match in universe and match not in block:
                found.add(match)

    return found
