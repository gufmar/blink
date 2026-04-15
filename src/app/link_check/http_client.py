"""HTTP client for checking discovered links."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class HttpCheckResult:
    status_code: int | None
    ok: bool
    error_message: str | None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class HttpLinkChecker:
    """Perform HTTP checks with configurable redirects and timeout."""

    def __init__(self, timeout_seconds: int, follow_redirects: bool) -> None:
        handlers = [] if follow_redirects else [_NoRedirectHandler()]
        self._opener = build_opener(*handlers)
        self._timeout_seconds = timeout_seconds

    def check(self, url: str) -> HttpCheckResult:
        request = Request(url=url, headers={"User-Agent": "Blink/3.0 LinkCheck"})
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                status_code = int(response.getcode())
                return HttpCheckResult(
                    status_code=status_code,
                    ok=200 <= status_code < 400,
                    error_message=None,
                )
        except HTTPError as exc:
            status_code = int(exc.code)
            return HttpCheckResult(
                status_code=status_code,
                ok=200 <= status_code < 400,
                error_message=str(exc),
            )
        except URLError as exc:
            return HttpCheckResult(status_code=None, ok=False, error_message=str(exc))
        except Exception as exc:  # noqa: BLE001
            return HttpCheckResult(status_code=None, ok=False, error_message=str(exc))
