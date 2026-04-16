from __future__ import annotations

from email.message import Message
from urllib.error import HTTPError

from app.link_check.http_client import HttpLinkChecker


class _RaisingOpener:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def open(self, request, timeout=None):  # type: ignore[no-untyped-def]
        raise self._exc


def _http_error(url: str, code: int, reason: str, headers: dict[str, str] | None = None) -> HTTPError:
    hdrs = Message()
    for key, value in (headers or {}).items():
        hdrs[key] = value
    return HTTPError(url=url, code=code, msg=reason, hdrs=hdrs, fp=None)


def test_http_checker_formats_cloudflare_challenge() -> None:
    checker = HttpLinkChecker(timeout_seconds=2, follow_redirects=True)
    checker._opener = _RaisingOpener(  # noqa: SLF001
        _http_error(
            "https://blocked.example",
            403,
            "Forbidden",
            headers={
                "server": "cloudflare",
                "cf-mitigated": "challenge",
                "cf-ray": "9ecd258cebe349b6-AMS",
            },
        )
    )
    result = checker.check("https://blocked.example")
    assert result.ok is False
    assert result.status_code == 403
    assert "Cloudflare challenge" in (result.error_message or "")
    assert "cf-ray=9ecd258cebe349b6-AMS" in (result.error_message or "")


def test_http_checker_formats_cloudflare_protected_403() -> None:
    checker = HttpLinkChecker(timeout_seconds=2, follow_redirects=True)
    checker._opener = _RaisingOpener(  # noqa: SLF001
        _http_error(
            "https://protected.example",
            403,
            "Forbidden",
            headers={"server": "cloudflare"},
        )
    )
    result = checker.check("https://protected.example")
    assert result.ok is False
    assert result.status_code == 403
    assert result.error_message == "HTTP 403 Forbidden - Cloudflare protected"
