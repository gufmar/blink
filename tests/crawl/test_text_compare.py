from __future__ import annotations

from app.crawl.text_compare import main_text_similarity_and_change_percent


def test_identical_texts_zero_change() -> None:
    sim, chg = main_text_similarity_and_change_percent("hello world", "hello world", max_chars=1000)
    assert sim > 0.999
    assert chg < 0.01


def test_unrelated_texts_high_change() -> None:
    sim, chg = main_text_similarity_and_change_percent("aaaa", "bbbb", max_chars=1000)
    assert sim < 0.5
    assert chg > 50.0
