"""Playwright-backed checker for discovered external links."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from app.link_check.http_client import HttpCheckResult
from app.render.playwright_client import BrowserSettings

WaitUntil = Literal["commit", "domcontentloaded"]


@dataclass(frozen=True)
class PlaywrightLinkCheckerConfig:
    navigation_timeout_seconds: int
    network_idle_seconds: int
    settle_wait_seconds: int
    wait_until: WaitUntil
    accept_partial_success_on_navigation_timeout: bool
    artifacts_dir: Path | None = None
    save_failure_screenshot: bool = False


def _playwright_transport_died(exc: BaseException) -> bool:
    """True when the browser/CDP websocket died; session must be discarded and reopened."""
    msg = str(exc).lower()
    return any(
        needle in msg
        for needle in (
            "websocket",
            "1006",
            "abnormal closure",
            "unexpected eof",
            "connection closed",
            "econnreset",
            "broken pipe",
            "target page, context or browser has been closed",
            "browser has been closed",
            "browser closed",
            "connection reset",
            "err_connection_reset",
            "ns_error_connection",
        )
    )


class PlaywrightLinkChecker:
    """Check links with a shared Playwright browser context."""

    def __init__(
        self,
        *,
        browser_settings: BrowserSettings,
        config: PlaywrightLinkCheckerConfig,
    ) -> None:
        self._browser_settings = browser_settings
        self._config = config
        self._navigation_timeout_ms = max(1000, int(config.navigation_timeout_seconds) * 1000)
        self._network_idle_timeout_ms = max(0, int(config.network_idle_seconds) * 1000)
        self._settle_wait_timeout_ms = max(0, int(config.settle_wait_seconds) * 1000)
        self._wait_until: WaitUntil = config.wait_until
        self._accept_partial = config.accept_partial_success_on_navigation_timeout
        self._blocked_netloc_contains = [value.lower() for value in browser_settings.block_request_netloc_contains]
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    def open_session(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for link checking. Install dependencies and run "
                "'playwright install chromium'."
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._browser_settings.headless)
        context_kwargs: dict[str, Any] = {
            "user_agent": self._browser_settings.user_agent,
            "viewport": {
                "width": self._browser_settings.viewport.width,
                "height": self._browser_settings.viewport.height,
            },
            "locale": self._browser_settings.locale,
            "timezone_id": self._browser_settings.timezone_id,
            "extra_http_headers": self._browser_settings.extra_http_headers,
        }
        if self._browser_settings.storage_state_path:
            state_path = Path(self._browser_settings.storage_state_path)
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)
        self._context = self._browser.new_context(**context_kwargs)

    def close_session(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            if self._browser is not None:
                self._browser.close()
                self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    def _discard_browser_session(self) -> None:
        """Tear down Playwright after transport loss; tolerates already-broken handles."""
        try:
            self.close_session()
        except Exception:  # noqa: BLE001
            pass
        self._context = None
        self._browser = None
        self._playwright = None

    def _should_block_request(self, request_url: str) -> bool:
        if not self._blocked_netloc_contains:
            return False
        host = urlsplit(request_url).netloc.lower()
        return any(needle in host for needle in self._blocked_netloc_contains)

    def _format_failure(
        self,
        *,
        status_code: int | None,
        response_headers: dict[str, str],
        fallback_error: str | None,
    ) -> str:
        if fallback_error:
            return fallback_error
        if status_code is None:
            return "Request failed"
        base = f"HTTP {status_code}"
        cf_mitigated = response_headers.get("cf-mitigated", "")
        cf_server = response_headers.get("server", "")
        if cf_mitigated.lower() == "challenge":
            cf_ray = response_headers.get("cf-ray", "")
            if cf_ray:
                return f"{base} - Cloudflare challenge (cf-ray={cf_ray})"
            return f"{base} - Cloudflare challenge"
        if status_code == 403 and "cloudflare" in cf_server.lower():
            return f"{base} - Cloudflare protected"
        if response_headers.get("x-vercel-mitigated", "").lower() == "challenge":
            return f"{base} - Vercel challenge"
        if "x-vercel-challenge-token" in response_headers:
            return f"{base} - Vercel challenge"
        return base

    def _meta(self, **kwargs: Any) -> str:
        payload = {"stage": "playwright", **kwargs}
        return json.dumps(payload, sort_keys=True)

    def _capture_failure_screenshot(self, page: Any, url: str) -> str | None:
        artifacts_dir = self._config.artifacts_dir
        if not self._config.save_failure_screenshot or artifacts_dir is None:
            return None
        host = urlsplit(url).netloc or "unknown-host"
        safe_host = "".join(ch if ch.isalnum() or ch in {"-", "."} else "_" for ch in host)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
        filename = f"linkcheck-failure-{safe_host}-{stamp}.png"
        output_path = artifacts_dir / filename
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(output_path), full_page=True)
            return filename
        except Exception as exc:  # noqa: BLE001
            if _playwright_transport_died(exc):
                raise
            return None

    def _check_one_page(self, url: str) -> HttpCheckResult:
        if self._context is None:
            self.open_session()
        assert self._context is not None
        page = self._context.new_page()
        page.route(
            "**/*",
            lambda route: route.abort("blockedbyclient")
            if self._should_block_request(route.request.url)
            else route.continue_(),
        )
        document_responses: list[tuple[int, dict[str, str]]] = []

        def on_response(response: Any) -> None:
            try:
                if response.request.resource_type != "document":
                    return
                status = int(response.status)
                headers = {k.lower(): v for k, v in response.headers.items()}
                document_responses.append((status, headers))
            except Exception:  # noqa: BLE001
                return

        page.on("response", on_response)
        status_code: int | None = None
        fallback_error: str | None = None
        response_headers: dict[str, str] = {}
        partial_timeout_recovery = False
        try:
            response = page.goto(
                url,
                timeout=self._navigation_timeout_ms,
                wait_until=self._wait_until,
            )
            if response is not None:
                status_code = response.status
                response_headers = {k.lower(): v for k, v in response.headers.items()}
            if self._network_idle_timeout_ms > 0:
                try:
                    page.wait_for_load_state("networkidle", timeout=self._network_idle_timeout_ms)
                except Exception as net_exc:  # noqa: BLE001
                    if _playwright_transport_died(net_exc):
                        raise
            if self._settle_wait_timeout_ms > 0:
                page.wait_for_timeout(self._settle_wait_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            if _playwright_transport_died(exc):
                raise
            fallback_error = str(exc)
            err_lower = fallback_error.lower()
            timed_out = "timeout" in err_lower or "timed out" in err_lower
            if (
                self._accept_partial
                and timed_out
                and document_responses
                and status_code is None
            ):
                last_status, last_headers = document_responses[-1]
                if 200 <= last_status < 400:
                    status_code = last_status
                    response_headers = last_headers
                    partial_timeout_recovery = True
                    fallback_error = None
        finally:
            ok = status_code is not None and 200 <= status_code < 400
            screenshot_file = None
            if not ok:
                screenshot_file = self._capture_failure_screenshot(page, url)
            error_message = None if ok else self._format_failure(
                status_code=status_code,
                response_headers=response_headers,
                fallback_error=fallback_error,
            )
            try:
                page.close()
            except Exception as close_exc:  # noqa: BLE001
                if _playwright_transport_died(close_exc):
                    raise

        check_meta = self._meta(
            wait_until=self._wait_until,
            partial_timeout_recovery=partial_timeout_recovery,
        )
        return HttpCheckResult(
            status_code=status_code,
            ok=ok,
            error_message=error_message,
            screenshot_file=screenshot_file,
            check_meta=check_meta,
        )

    def check(self, url: str) -> HttpCheckResult:
        """Check URL; on CDP/websocket loss discard the session once and retry the same URL."""
        for attempt in range(2):
            try:
                return self._check_one_page(url)
            except Exception as exc:  # noqa: BLE001
                if not _playwright_transport_died(exc):
                    raise
                self._discard_browser_session()
                if attempt == 1:
                    return HttpCheckResult(
                        status_code=None,
                        ok=False,
                        error_message=str(exc),
                        screenshot_file=None,
                        check_meta=self._meta(
                            wait_until=self._wait_until,
                            transport_error=True,
                            transport_retry_exhausted=True,
                        ),
                    )
        raise RuntimeError("unreachable")  # pragma: no cover
