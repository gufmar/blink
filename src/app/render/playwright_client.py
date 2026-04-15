"""Playwright page rendering adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any
from urllib.parse import urlsplit


class RenderError(RuntimeError):
    """Raised when page rendering fails."""


@dataclass(frozen=True)
class BrowserViewport:
    width: int
    height: int


@dataclass(frozen=True)
class BrowserSettings:
    user_agent: str
    viewport: BrowserViewport
    locale: str
    timezone_id: str
    extra_http_headers: dict[str, str]
    storage_state_path: str | None
    persist_storage_state: bool
    headless: bool
    block_request_netloc_contains: list[str]


@dataclass(frozen=True)
class ObservabilitySettings:
    log_console: bool
    log_non_2xx_responses: bool
    log_request_failures: bool
    save_failure_screenshot: bool
    save_failure_html: bool


@dataclass(frozen=True)
class RenderResult:
    """Rendered page payload used by crawl runner."""

    requested_url: str
    url: str
    status_code: int | None
    html: str
    response_headers: dict[str, str]
    challenge_detected: bool
    request_failures: list[str]
    console_messages: list[str]
    screenshot_path: str | None
    html_snapshot_path: str | None


class PlaywrightRenderer:
    """Render pages using one shared Playwright context per run."""

    def __init__(
        self,
        navigation_timeout_seconds: int,
        network_idle_seconds: int,
        playwright_wait_seconds: int,
        browser_settings: BrowserSettings,
        observability: ObservabilitySettings,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._navigation_timeout_ms = navigation_timeout_seconds * 1000
        self._network_idle_timeout_ms = network_idle_seconds * 1000
        self._playwright_wait_timeout_ms = playwright_wait_seconds * 1000
        self._browser_settings = browser_settings
        self._observability = observability
        self._artifacts_dir = artifacts_dir
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._blocked_netloc_contains = [value.lower() for value in browser_settings.block_request_netloc_contains]

    def open_session(self) -> None:
        """Open browser + context once for the full crawl run."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RenderError(
                "Playwright is required for crawl rendering. "
                "Install dependencies and run 'playwright install chromium'."
            ) from exc

        try:
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
        except Exception as exc:  # noqa: BLE001
            raise RenderError(f"Failed to open browser session: {exc}") from exc

    def close_session(self) -> None:
        """Close context and browser, optionally persisting storage state."""
        try:
            if self._context is not None:
                if self._browser_settings.persist_storage_state and self._browser_settings.storage_state_path:
                    state_path = Path(self._browser_settings.storage_state_path)
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    self._context.storage_state(path=str(state_path))
                self._context.close()
        finally:
            self._context = None
            if self._browser is not None:
                self._browser.close()
                self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    def _save_artifacts(self, page: Any, content: str, challenge: bool, non_2xx: bool) -> tuple[str | None, str | None]:
        if self._artifacts_dir is None:
            return None, None
        if not (challenge or non_2xx):
            return None, None
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(int(time() * 1000))
        screenshot_path: str | None = None
        html_path: str | None = None
        if self._observability.save_failure_screenshot:
            screenshot_file = self._artifacts_dir / f"render-{stamp}.png"
            page.screenshot(path=str(screenshot_file), full_page=True)
            screenshot_path = str(screenshot_file)
        if self._observability.save_failure_html:
            html_file = self._artifacts_dir / f"render-{stamp}.html"
            html_file.write_text(content, encoding="utf-8")
            html_path = str(html_file)
        return screenshot_path, html_path

    def _should_block_request(self, request_url: str) -> bool:
        if not self._blocked_netloc_contains:
            return False
        host = urlsplit(request_url).netloc.lower()
        return any(needle in host for needle in self._blocked_netloc_contains)

    def render(self, url: str) -> RenderResult:
        """Render one URL within the shared run session."""
        if self._context is None:
            raise RenderError("Renderer session is not open. Call open_session() first.")
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:
            raise RenderError(
                "Playwright is required for crawl rendering. "
                "Install dependencies and run 'playwright install chromium'."
            ) from exc

        request_failures: list[str] = []
        console_messages: list[str] = []
        page = self._context.new_page()
        page.route(
            "**/*",
            lambda route: route.abort("blockedbyclient")
            if self._should_block_request(route.request.url)
            else route.continue_(),
        )
        page.on(
            "requestfailed",
            lambda request: request_failures.append(f"{request.url} -> {request.failure}")
            if (
                request.failure is None
                or (
                    request.failure != "net::ERR_BLOCKED_BY_CLIENT"
                    and not self._should_block_request(request.url)
                )
            )
            else None,
        )
        if self._observability.log_console:
            page.on("console", lambda msg: console_messages.append(msg.text))

        try:
            response = page.goto(url, timeout=self._navigation_timeout_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=self._network_idle_timeout_ms)
            page.wait_for_timeout(self._playwright_wait_timeout_ms)
            content = page.content()
            status_code = response.status if response else None
            headers = {k.lower(): v for k, v in (response.headers.items() if response else {})}
            challenge = headers.get("x-vercel-mitigated", "").lower() == "challenge" or "x-vercel-challenge-token" in headers
            non_2xx = status_code is None or not (200 <= status_code < 300)
            screenshot_path, html_snapshot_path = self._save_artifacts(page, content, challenge, non_2xx)
            return RenderResult(
                requested_url=url,
                url=page.url,
                status_code=status_code,
                html=content,
                response_headers=headers,
                challenge_detected=challenge,
                request_failures=request_failures,
                console_messages=console_messages,
                screenshot_path=screenshot_path,
                html_snapshot_path=html_snapshot_path,
            )
        except PlaywrightTimeoutError as exc:
            raise RenderError(f"Timeout while rendering {url}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RenderError(f"Failed to render {url}: {exc}") from exc
        finally:
            page.close()
