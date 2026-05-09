from __future__ import annotations

from app.link_check.preflight import (
    normalize_content_type,
    path_matches_extension,
    should_skip_playwright_for_asset,
)


def test_normalize_content_type_strips_charset() -> None:
    assert normalize_content_type('text/html; charset="utf-8"') == "text/html"


def test_path_matches_extension() -> None:
    assert path_matches_extension("https://x.example/a/b/file.zip", [".zip"]) is True
    assert path_matches_extension("https://x.example/page", [".zip"]) is False


def test_should_skip_playwright_for_asset() -> None:
    assert (
        should_skip_playwright_for_asset(
            url="https://cdn.example/blob.bin",
            content_type="application/zip",
            skip_content_types=["application/zip"],
            skip_extensions=[".pdf"],
        )
        is True
    )
    assert (
        should_skip_playwright_for_asset(
            url="https://cdn.example/a.pdf",
            content_type=None,
            skip_content_types=["application/zip"],
            skip_extensions=[".pdf"],
        )
        is True
    )
