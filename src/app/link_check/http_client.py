"""HTTP client for checking discovered links."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class HttpCheckResult:
    status_code: int | None
    ok: bool
    error_message: str | None
    screenshot_file: str | None = None
    check_meta: str | None = None


def _http_check_meta() -> str:
    return json.dumps({"method": "urllib", "stage": "http"}, sort_keys=True)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class HttpLinkChecker:
    """Perform HTTP checks with configurable redirects and timeout."""

    def __init__(
        self,
        timeout_seconds: int,
        follow_redirects: bool,
        *,
        artifacts_dir: Path | None = None,
        save_failure_screenshot: bool = False,
        user_agent: str | None = None,
    ) -> None:
        handlers = [] if follow_redirects else [_NoRedirectHandler()]
        self._opener = build_opener(*handlers)
        self._timeout_seconds = timeout_seconds
        self._artifacts_dir = artifacts_dir
        self._save_failure_screenshot = save_failure_screenshot
        self._user_agent = user_agent or "Blink/3.0 LinkCheck"

    def _format_http_error(self, exc: HTTPError) -> str:
        status_code = int(exc.code)
        reason = str(exc.reason) if getattr(exc, "reason", None) else ""
        base = f"HTTP {status_code}" if not reason else f"HTTP {status_code} {reason}"

        headers = getattr(exc, "headers", None)
        if headers is not None:
            cf_mitigated = headers.get("cf-mitigated")
            server = headers.get("server")
            if cf_mitigated and str(cf_mitigated).lower() == "challenge":
                cf_ray = headers.get("cf-ray")
                details = "Cloudflare challenge"
                if cf_ray:
                    details += f" (cf-ray={cf_ray})"
                return f"{base} - {details}"
            if status_code == 403 and server and "cloudflare" in str(server).lower():
                return f"{base} - Cloudflare protected"
        return base

    def _capture_failure_screenshot(self, url: str) -> str | None:
        if not self._save_failure_screenshot or self._artifacts_dir is None:
            return None
        host = urlsplit(url).netloc or "unknown-host"
        safe_host = "".join(ch if ch.isalnum() or ch in {"-", "."} else "_" for ch in host)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
        filename = f"linkcheck-failure-{safe_host}-{stamp}.png"
        output_path = self._artifacts_dir / filename
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=max(1000, self._timeout_seconds * 1000))
                page.screenshot(path=str(output_path), full_page=True)
                browser.close()
            return filename
        except Exception:  # noqa: BLE001
            return None

    def check(self, url: str) -> HttpCheckResult:
        request = Request(url=url, headers={"User-Agent": self._user_agent})
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                status_code = int(response.getcode())
                screenshot_file = None
                if not (200 <= status_code < 400):
                    screenshot_file = self._capture_failure_screenshot(url)
                return HttpCheckResult(
                    status_code=status_code,
                    ok=200 <= status_code < 400,
                    error_message=None,
                    screenshot_file=screenshot_file,
                    check_meta=_http_check_meta(),
                )
        except HTTPError as exc:
            status_code = int(exc.code)
            screenshot_file = self._capture_failure_screenshot(url)
            return HttpCheckResult(
                status_code=status_code,
                ok=200 <= status_code < 400,
                error_message=self._format_http_error(exc),
                screenshot_file=screenshot_file,
                check_meta=_http_check_meta(),
            )
        except URLError as exc:
            return HttpCheckResult(
                status_code=None,
                ok=False,
                error_message=str(exc),
                screenshot_file=self._capture_failure_screenshot(url),
                check_meta=_http_check_meta(),
            )
        except Exception as exc:  # noqa: BLE001
            return HttpCheckResult(
                status_code=None,
                ok=False,
                error_message=str(exc),
                screenshot_file=self._capture_failure_screenshot(url),
                check_meta=_http_check_meta(),
            )
