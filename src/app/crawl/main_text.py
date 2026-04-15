"""Main-body plain text extraction for crawled HTML."""

from __future__ import annotations

import re
from typing import Literal

MainTextExtractor = Literal["regex", "trafilatura"]


def extract_main_text_regex(html: str) -> str:
    """Strip tags with regex; collapses whitespace (legacy MVP behavior)."""
    no_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", no_tags).strip()


def extract_main_text_trafilatura(html: str) -> str:
    """Use trafilatura to extract main article text; returns empty string on failure."""
    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    return (text or "").strip()


def extract_page_main_text(html: str, extractor: MainTextExtractor) -> str:
    """Extract readable main text; trafilatura falls back to regex if output is too short."""
    if extractor == "trafilatura":
        primary = extract_main_text_trafilatura(html)
        if len(primary) < 32:
            return extract_main_text_regex(html)
        return primary
    return extract_main_text_regex(html)
