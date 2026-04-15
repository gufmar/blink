from __future__ import annotations

from app.crawl.main_text import extract_page_main_text


def test_regex_extractor_strips_tags() -> None:
    html = "<html><body><p>Hello <b>world</b></p></body></html>"
    out = extract_page_main_text(html, "regex")
    assert "Hello" in out
    assert "world" in out
    assert "<" not in out


def test_trafilatura_extractor_returns_plain_text() -> None:
    html = """
    <html><head><title>T</title></head>
    <body><article><h1>Title</h1><p>Paragraph one.</p></article></body></html>
    """
    out = extract_page_main_text(html, "trafilatura")
    assert "Paragraph" in out or "Title" in out
    assert "<article>" not in out
