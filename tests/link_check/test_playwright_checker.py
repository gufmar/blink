from __future__ import annotations

from app.link_check.playwright_checker import (
    PlaywrightLinkChecker,
    PlaywrightLinkCheckerConfig,
    _playwright_transport_died,
)
from app.render.playwright_client import BrowserSettings, BrowserViewport


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}


class _FakePage:
    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        goto_exc: Exception | None = None,
        wait_exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._goto_exc = goto_exc
        self._wait_exc = wait_exc
        self.screenshot_calls = 0

    def route(self, _pattern: str, _handler) -> None:  # noqa: ANN001
        return

    def on(self, _event: str, _handler) -> None:  # noqa: ANN001
        return

    def goto(self, _url: str, *, timeout: int, wait_until: str):  # noqa: ANN001
        _ = timeout
        _ = wait_until
        if self._goto_exc is not None:
            raise self._goto_exc
        return self._response

    def wait_for_load_state(self, _state: str, *, timeout: int) -> None:  # noqa: ANN001
        _ = timeout
        if self._wait_exc is not None:
            raise self._wait_exc

    def wait_for_timeout(self, _timeout: int) -> None:
        return

    def screenshot(self, *, path: str, full_page: bool) -> None:
        _ = path
        _ = full_page
        self.screenshot_calls += 1

    def close(self) -> None:
        return


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def new_page(self) -> _FakePage:
        return self._page

    def close(self) -> None:
        return


def _checker_with_context(page: _FakePage) -> PlaywrightLinkChecker:
    checker = PlaywrightLinkChecker(
        browser_settings=BrowserSettings(
            user_agent="Blink/3.0",
            viewport=BrowserViewport(width=1200, height=800),
            locale="en-US",
            timezone_id="UTC",
            extra_http_headers={},
            storage_state_path=None,
            persist_storage_state=False,
            headless=True,
            block_request_netloc_contains=[],
        ),
        config=PlaywrightLinkCheckerConfig(
            navigation_timeout_seconds=5,
            network_idle_seconds=1,
            settle_wait_seconds=0,
            wait_until="commit",
            accept_partial_success_on_navigation_timeout=True,
            artifacts_dir=None,
            save_failure_screenshot=True,
        ),
    )
    checker._context = _FakeContext(page)  # noqa: SLF001
    return checker


def test_playwright_checker_returns_success_for_2xx() -> None:
    page = _FakePage(response=_FakeResponse(200))
    checker = _checker_with_context(page)
    result = checker.check("https://ok.example")
    assert result.ok is True
    assert result.status_code == 200
    assert result.error_message is None
    assert result.screenshot_file is None


def test_playwright_checker_formats_cloudflare_challenge() -> None:
    page = _FakePage(
        response=_FakeResponse(
            403,
            headers={
                "cf-mitigated": "challenge",
                "cf-ray": "abc123",
                "server": "cloudflare",
            },
        )
    )
    checker = _checker_with_context(page)
    result = checker.check("https://blocked.example")
    assert result.ok is False
    assert result.status_code == 403
    assert "Cloudflare challenge" in (result.error_message or "")
    assert "cf-ray=abc123" in (result.error_message or "")


def test_playwright_checker_handles_navigation_error() -> None:
    page = _FakePage(goto_exc=RuntimeError("net::ERR_CONNECTION_REFUSED"))
    checker = _checker_with_context(page)
    result = checker.check("https://down.example")
    assert result.ok is False
    assert result.status_code is None
    assert "ERR_CONNECTION_REFUSED" in (result.error_message or "")


class _FakeDocResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.headers = {}
        self.request = type("Req", (), {"resource_type": "document"})()


class _FakePageTimeoutPartial:
    def __init__(self) -> None:
        self.screenshot_calls = 0
        self._response_handler = None

    def route(self, _pattern: str, _handler) -> None:  # noqa: ANN001
        return

    def on(self, event: str, handler) -> None:  # noqa: ANN001
        if event == "response":
            self._response_handler = handler

    def goto(self, _url: str, *, timeout: int, wait_until: str):  # noqa: ANN001
        if self._response_handler is not None:
            self._response_handler(_FakeDocResponse(200))
        raise RuntimeError("Page.goto: Timeout 10000ms exceeded")

    def wait_for_load_state(self, _state: str, *, timeout: int) -> None:  # noqa: ANN001
        return

    def wait_for_timeout(self, _timeout: int) -> None:
        return

    def screenshot(self, *, path: str, full_page: bool) -> None:
        _ = path
        _ = full_page
        self.screenshot_calls += 1

    def close(self) -> None:
        return


def test_playwright_checker_partial_success_on_navigation_timeout() -> None:
    checker = _checker_with_context(_FakePageTimeoutPartial())
    result = checker.check("https://slow.example")
    assert result.ok is True
    assert result.status_code == 200
    assert result.error_message is None
    assert result.screenshot_file is None
    assert result.check_meta is not None
    assert "partial_timeout_recovery" in result.check_meta


def test_playwright_transport_died_detects_websocket_close() -> None:
    exc = RuntimeError("websocket: close 1006 (abnormal closure): unexpected EOF")
    assert _playwright_transport_died(exc) is True
    assert _playwright_transport_died(RuntimeError("net::ERR_CONNECTION_REFUSED")) is False


def _checker_for_transport_retry() -> PlaywrightLinkChecker:
    return PlaywrightLinkChecker(
        browser_settings=BrowserSettings(
            user_agent="Blink/3.0",
            viewport=BrowserViewport(width=1200, height=800),
            locale="en-US",
            timezone_id="UTC",
            extra_http_headers={},
            storage_state_path=None,
            persist_storage_state=False,
            headless=True,
            block_request_netloc_contains=[],
        ),
        config=PlaywrightLinkCheckerConfig(
            navigation_timeout_seconds=5,
            network_idle_seconds=0,
            settle_wait_seconds=0,
            wait_until="commit",
            accept_partial_success_on_navigation_timeout=False,
            artifacts_dir=None,
            save_failure_screenshot=False,
        ),
    )


def test_playwright_checker_retries_after_websocket_transport_loss() -> None:
    page_fail = _FakePage(
        goto_exc=RuntimeError("websocket: close 1006 (abnormal closure): unexpected EOF"),
    )
    page_ok = _FakePage(response=_FakeResponse(200))
    checker = _checker_for_transport_retry()
    opens: list[int] = []

    def fake_open_session() -> None:
        if checker._context is not None:
            return
        opens.append(1)
        checker._context = _FakeContext(page_fail) if len(opens) == 1 else _FakeContext(page_ok)

    checker.open_session = fake_open_session  # type: ignore[method-assign]

    result = checker.check("https://recover.example")
    assert len(opens) == 2
    assert result.ok is True
    assert result.status_code == 200


def test_playwright_checker_returns_failure_after_two_transport_losses() -> None:
    transport = RuntimeError("websocket: close 1006 (abnormal closure): unexpected EOF")
    page_fail = _FakePage(goto_exc=transport)
    checker = _checker_for_transport_retry()

    def fake_open_session() -> None:
        if checker._context is not None:
            return
        checker._context = _FakeContext(page_fail)

    checker.open_session = fake_open_session  # type: ignore[method-assign]

    result = checker.check("https://dead.example")
    assert result.ok is False
    assert result.status_code is None
    assert "1006" in (result.error_message or "")
    assert result.check_meta is not None
    assert "transport_retry_exhausted" in result.check_meta
