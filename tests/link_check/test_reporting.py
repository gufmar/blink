from __future__ import annotations

from datetime import datetime

from app.link_check.reporting import build_link_check_report, classify_error_category, report_filename
from app.persistence.repository import LinkCheckResultRecord


def test_classify_error_category_http_and_transport() -> None:
    assert classify_error_category(404, "HTTP Error 404", ok=False) == "client"
    assert classify_error_category(503, "HTTP Error 503", ok=False) == "server"
    assert classify_error_category(None, "timed out while connecting", ok=False) == "timeout"
    assert classify_error_category(None, "Name or service not known", ok=False) == "connection"
    assert classify_error_category(None, "unexpected failure", ok=False) == "other"
    assert classify_error_category(200, None, ok=True) is None


def test_build_link_check_report_groups_errors_and_sources() -> None:
    results = [
        LinkCheckResultRecord(
            row_id=1,
            crawl_link_id=10,
            crawl_run_id=55,
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
            target_url="https://b.example",
            status_code=None,
            ok=False,
            error_message="<urlopen error timed out>",
            checked_at="2026-04-15 06:00:10",
        ),
        LinkCheckResultRecord(
            row_id=3,
            crawl_link_id=12,
            crawl_run_id=55,
            target_url="https://ok.example",
            status_code=200,
            ok=True,
            error_message=None,
            checked_at="2026-04-15 06:00:20",
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
        results=results,
        source_pages_by_target={
            "https://a.example": ["https://cardano.org/a"],
            "https://b.example": ["https://cardano.org/b", "https://cardano.org/c"],
        },
    )
    assert payload["summary"]["categories"]["client"] == 1
    assert payload["summary"]["categories"]["timeout"] == 1
    assert payload["summary"]["categories"]["server"] == 0
    assert payload["provenance_stats"]["distinct_external_urls_checked"] == 3
    assert payload["provenance_stats"]["distinct_source_pages_referenced"] == 3
    assert len(payload["errors"]["client"]) == 1
    assert len(payload["errors"]["timeout"]) == 1
    assert payload["errors"]["client"][0]["source_pages"] == ["https://cardano.org/a"]


def test_report_filename_format() -> None:
    name = report_filename("cardano.org", datetime(2026, 4, 15, 6, 25))
    assert name == "report_cardano.org_2026-04-15_06-25.json"

