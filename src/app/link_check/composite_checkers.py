"""Composable link checkers (preflight asset skip, HTTP-then-Playwright hybrid)."""

from __future__ import annotations

from typing import Any

from app.link_check.http_client import HttpCheckResult, HttpLinkChecker
from app.link_check.playwright_checker import PlaywrightLinkChecker
from app.link_check.preflight import (
    is_probably_html,
    preflight_ok_meta,
    run_preflight,
    should_skip_playwright_for_asset,
)
from app.models.job_config import JobConfig


class PreflightAssetSkippingChecker:
    """Skip Playwright for configured binary/download types when preflight succeeds."""

    def __init__(
        self,
        *,
        config: JobConfig,
        delegate: Any,
    ) -> None:
        self._config = config
        self._delegate = delegate

    def open_session(self) -> None:
        opener = getattr(self._delegate, "open_session", None)
        if callable(opener):
            opener()

    def close_session(self) -> None:
        closer = getattr(self._delegate, "close_session", None)
        if callable(closer):
            closer()

    def check(self, url: str) -> HttpCheckResult:
        lc = self._config["link_check"]
        pre = lc["preflight"]
        if not pre["enabled"]:
            return self._delegate.check(url)

        ua = self._config["crawl"]["user_agent"]
        pf = run_preflight(
            url,
            user_agent=ua,
            timeout_seconds=lc["request_timeout_seconds"],
            follow_redirects=lc["follow_redirects"],
        )
        if pf.status_code is not None and 200 <= pf.status_code < 400:
            if should_skip_playwright_for_asset(
                url=url,
                content_type=pf.content_type,
                skip_content_types=pre["skip_playwright_content_types"],
                skip_extensions=pre["skip_playwright_path_extensions"],
            ):
                return HttpCheckResult(
                    status_code=pf.status_code,
                    ok=True,
                    error_message=None,
                    screenshot_file=None,
                    check_meta=preflight_ok_meta("asset_preflight"),
                )
        return self._delegate.check(url)


class HttpThenPlaywrightChecker:
    """Cheap HTTP first; Playwright when policy says HTML verification or HTTP retry."""

    def __init__(
        self,
        *,
        config: JobConfig,
        http_checker: HttpLinkChecker,
        playwright_checker: PlaywrightLinkChecker,
    ) -> None:
        self._config = config
        self._http = http_checker
        self._pw = playwright_checker

    def open_session(self) -> None:
        self._pw.open_session()

    def close_session(self) -> None:
        self._pw.close_session()

    def _should_retry_playwright(self, result: HttpCheckResult) -> bool:
        hy = self._config["link_check"]["hybrid"]
        if result.status_code is not None and result.status_code in hy["retry_playwright_http_status"]:
            return True
        if result.status_code is None and hy["retry_playwright_on_connection_error"]:
            return True
        return False

    def check(self, url: str) -> HttpCheckResult:
        lc = self._config["link_check"]
        pre = lc["preflight"]
        hy = lc["hybrid"]
        ua = self._config["crawl"]["user_agent"]

        if pre["enabled"]:
            pf = run_preflight(
                url,
                user_agent=ua,
                timeout_seconds=lc["request_timeout_seconds"],
                follow_redirects=lc["follow_redirects"],
            )
            if pf.error_message is None and pf.status_code is not None and 200 <= pf.status_code < 400:
                if should_skip_playwright_for_asset(
                    url=url,
                    content_type=pf.content_type,
                    skip_content_types=pre["skip_playwright_content_types"],
                    skip_extensions=pre["skip_playwright_path_extensions"],
                ):
                    return HttpCheckResult(
                        status_code=pf.status_code,
                        ok=True,
                        error_message=None,
                        screenshot_file=None,
                        check_meta=preflight_ok_meta("asset_preflight"),
                    )
                if is_probably_html(pf.content_type) and hy["run_playwright_when_preflight_html"]:
                    return self._pw.check(url)
                if pf.content_type and not is_probably_html(pf.content_type):
                    return HttpCheckResult(
                        status_code=pf.status_code,
                        ok=True,
                        error_message=None,
                        screenshot_file=None,
                        check_meta=preflight_ok_meta("non_html_preflight"),
                    )
                if hy["run_playwright_when_http_ok_unknown_type"]:
                    return self._pw.check(url)
                return HttpCheckResult(
                    status_code=pf.status_code,
                    ok=True,
                    error_message=None,
                    screenshot_file=None,
                    check_meta=preflight_ok_meta("preflight_unknown_type"),
                )

            if pf.error_message is not None and hy["retry_playwright_on_connection_error"]:
                return self._pw.check(url)

        http_result = self._http.check(url)
        if http_result.ok:
            if hy["run_playwright_when_http_ok_unknown_type"]:
                return self._pw.check(url)
            return http_result

        if self._should_retry_playwright(http_result):
            return self._pw.check(url)
        return http_result
