from __future__ import annotations

from app.render.playwright_client import BrowserSettings, BrowserViewport, ObservabilitySettings, PlaywrightRenderer


def test_should_block_request_by_netloc() -> None:
    renderer = PlaywrightRenderer(
        navigation_timeout_seconds=10,
        network_idle_seconds=2,
        playwright_wait_seconds=1,
        browser_settings=BrowserSettings(
            user_agent="UA",
            viewport=BrowserViewport(width=800, height=600),
            locale="en-US",
            timezone_id="UTC",
            extra_http_headers={},
            storage_state_path=None,
            persist_storage_state=False,
            headless=True,
            block_request_netloc_contains=["google-analytics.com"],
        ),
        observability=ObservabilitySettings(
            log_console=False,
            log_non_2xx_responses=True,
            log_request_failures=True,
            save_failure_screenshot=False,
            save_failure_html=False,
        ),
    )

    assert renderer._should_block_request("https://region1.google-analytics.com/g/collect") is True  # noqa: SLF001
    assert renderer._should_block_request("https://cardano.org/") is False  # noqa: SLF001
