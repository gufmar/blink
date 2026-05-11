from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.link_check.preflight import (
    normalize_content_type,
    path_matches_extension,
    run_preflight,
    should_skip_playwright_for_asset,
)


def test_normalize_content_type_strips_charset() -> None:
    assert normalize_content_type('text/html; charset="utf-8"') == "text/html"


def test_path_matches_extension() -> None:
    assert path_matches_extension("https://x.example/a/b/file.zip", [".zip"]) is True
    assert path_matches_extension("https://x.example/page", [".zip"]) is False


def test_run_preflight_head_oserror_timeout_returns_error_result() -> None:
    """HEAD can raise TimeoutError/OSError from getresponse() without URLError wrapping."""
    mock_opener = MagicMock()
    mock_opener.open.side_effect = TimeoutError("The read operation timed out")
    with patch("app.link_check.preflight.build_opener", return_value=mock_opener):
        out = run_preflight(
            "https://example.com/x",
            user_agent="test-ua",
            timeout_seconds=5,
            follow_redirects=True,
        )
    assert out.status_code is None
    assert out.error_message is not None
    assert "timed out" in out.error_message.lower()


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
