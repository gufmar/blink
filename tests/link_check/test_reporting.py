from __future__ import annotations

from datetime import datetime

from app.link_check.reporting import (
    build_link_check_report,
    classify_error_category,
    is_browser_engine_error,
    report_filename,
)
from app.persistence.repository import LinkCheckResultRecord


def test_is_browser_engine_error_playwright_without_http_status() -> None:
    meta = '{"stage": "playwright", "wait_until": "commit"}'
    assert is_browser_engine_error(
        status_code=None,
        error_message="net::ERR_NAME_NOT_RESOLVED",
        check_meta=meta,
    )
    assert not is_browser_engine_error(
        status_code=403,
        error_message="HTTP 403 - Cloudflare challenge",
        check_meta=meta,
    )
    assert not is_browser_engine_error(
        status_code=None,
        error_message="net::ERR_NAME_NOT_RESOLVED",
        check_meta='{"stage": "http"}',
    )


def test_classify_error_category_http_and_transport() -> None:
    assert classify_error_category(404, "HTTP Error 404", ok=False) == "client"
    assert classify_error_category(503, "HTTP Error 503", ok=False) == "server"
    assert classify_error_category(None, "timed out while connecting", ok=False) == "timeout"
    assert classify_error_category(None, "Name or service not known", ok=False) == "client"
    assert classify_error_category(None, "unexpected failure", ok=False) == "other"
    assert classify_error_category(200, None, ok=True) is None


def test_build_link_check_report_groups_errors_and_sources() -> None:
    results = [
        LinkCheckResultRecord(
            row_id=1,
            crawl_link_id=10,
            crawl_run_id=55,
            link_check_run_id=None,
            target_url="https://a.example",
            status_code=404,
            ok=False,
            error_message="HTTP Error 404",
            checked_at="2026-04-15 06:00:00",
        ),
        LinkCheckResultRecord(
            row_id=2,
            crawl_link_id=11,
            crawl_run_id=55,
            link_check_run_id=None,
            target_url="https://b.example",
            status_code=None,
            ok=False,
            error_message="<urlopen error timed out>",
            checked_at="2026-04-15 06:00:10",
            decision_state="pending_tolerance",
            decision_reason="tolerance-pending",
        ),
        LinkCheckResultRecord(
            row_id=3,
            crawl_link_id=12,
            crawl_run_id=55,
            link_check_run_id=None,
            target_url="https://ok.example",
            status_code=200,
            ok=True,
            error_message=None,
            checked_at="2026-04-15 06:00:20",
        ),
        LinkCheckResultRecord(
            row_id=4,
            crawl_link_id=13,
            crawl_run_id=55,
            link_check_run_id=None,
            target_url="https://ignored.example",
            status_code=404,
            ok=False,
            error_message="HTTP Error 404",
            checked_at="2026-04-15 06:00:30",
            decision_state="ignored",
            decision_reason="ignore-rule:1",
        ),
    ]
    payload = build_link_check_report(
        job_id="cardano.org",
        base_url="https://cardano.org",
        crawl_run_id=55,
        run_started_at="2026-04-15 05:00:00",
        run_finished_at="2026-04-15 05:30:00",
        link_check_started_at="2026-04-15T06:00:00+00:00",
        link_check_finished_at="2026-04-15T06:10:00+00:00",
        checked=3,
        passed=1,
        failed=1,
        errored=1,
        skipped=0,
        ignored=1,
        pending_tolerance=1,
        reportable_failures=1,
        results=results,
        source_refs_by_target={
            "https://a.example": [{"page_url": "https://cardano.org/a", "anchor_text": "A text"}],
            "https://b.example": [
                {"page_url": "https://cardano.org/b", "anchor_text": None},
                {"page_url": "https://cardano.org/c", "anchor_text": "C text"},
            ],
            "https://ignored.example": [{"page_url": "https://cardano.org/d", "anchor_text": "D text"}],
        },
    )
    assert payload["job"]["summary"]["categories"]["client"] == 1
    assert payload["job"]["summary"]["categories"]["timeout"] == 0
    assert payload["job"]["summary"]["categories"]["server"] == 0
    assert payload["job"]["summary"]["ignored"] == 1
    assert payload["job"]["summary"]["pending_tolerance"] == 1
    assert payload["job"]["summary"]["reportable_failures"] == 1
    assert payload["job"]["provenance_stats"]["distinct_external_urls_checked"] == 4
    assert payload["job"]["provenance_stats"]["distinct_source_pages_referenced"] == 4
    assert len(payload["errors"]["client"]) == 1
    assert len(payload["errors"]["timeout"]) == 0
    assert payload["errors"]["client"][0]["target_url"] == "https://a.example"
    assert payload["errors"]["client"][0]["blinks"][0]["anchor_text"] == "A text"
    assert len(payload["job"]["suppressed"]["ignored"]) == 1
    assert len(payload["job"]["suppressed"]["pending_tolerance"]) == 1


def test_report_filename_format() -> None:
    name = report_filename("cardano.org", datetime(2026, 4, 15, 6, 25))
    assert name == "report_cardano.org_2026-04-15_06-25.json"

