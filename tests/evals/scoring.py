"""Scoring shared by run_evals.py and summarize.py.

Answers are normalised before matching because models emit typographic
variants of the same value: dates with U+2011 non-breaking hyphens and
thousands with U+202F narrow no-break spaces are the right answer and must not
be scored as missing.
"""

import re
import unicodedata

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_SPACES = dict.fromkeys(map(ord, "    "), " ")


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_SPACES)


def score(question: dict, answer: str) -> tuple[list[str], list[str]]:
    """Return (missing patterns, forbidden patterns found); correct when both are empty."""
    text = normalise(answer)
    missing = [p for p in question["expect_all"] if not re.search(p, text, re.I | re.S)]
    forbidden = [p for p in question.get("expect_none", []) if re.search(p, text, re.I | re.S)]
    return missing, forbidden
