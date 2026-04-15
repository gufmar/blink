"""Compare normalized main text between crawl runs."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def main_text_similarity_and_change_percent(
    current: str,
    previous: str,
    *,
    max_chars: int,
) -> tuple[float, float]:
    """Return (similarity in 0..1, change_percent = (1 - similarity) * 100)."""
    a = normalize_for_compare(current[:max_chars])
    b = normalize_for_compare(previous[:max_chars])
    if not a and not b:
        return 1.0, 0.0
    if not a or not b:
        return 0.0, 100.0
    similarity = float(SequenceMatcher(None, a, b).ratio())
    change_percent = (1.0 - similarity) * 100.0
    return similarity, change_percent
