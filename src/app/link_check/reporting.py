"""Build and write JSON link-check reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.persistence.repository import LinkCheckResultRecord

_TIMEOUT_TOKENS = ("timed out", "timeout")
_CONNECTION_TOKENS = (
    "connection refused",
    "name or service not known",
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "network is unreachable",
    "connection reset",
    "ssl",
    "certificate",
    "too many redirects",
    "dns",
    "urlopen error",
)


def classify_error_category(status_code: int | None, error_message: str | None, ok: bool) -> str | None:
    """Map one check result to a deterministic error category."""
    if ok:
        return None
    if status_code is not None:
        if 400 <= status_code < 500:
            return "client"
        if 500 <= status_code < 600:
            return "server"
        return "other"
    message = (error_message or "").lower()
    if any(token in message for token in _TIMEOUT_TOKENS):
        return "timeout"
    if any(token in message for token in _CONNECTION_TOKENS):
        return "connection"
    return "other"


def build_link_check_report(
    *,
    job_id: str,
    base_url: str,
    crawl_run_id: int,
    run_started_at: str | None,
    run_finished_at: str | None,
    link_check_started_at: str | None,
    link_check_finished_at: str | None,
    checked: int,
    passed: int,
    failed: int,
    errored: int,
    skipped: int,
    results: list[LinkCheckResultRecord],
    source_pages_by_target: dict[str, list[str]],
) -> dict[str, Any]:
    """Build a v3 JSON report payload for one link-check run."""
    categories: dict[str, int] = {"client": 0, "server": 0, "timeout": 0, "connection": 0, "other": 0}
    grouped_errors: dict[str, list[dict[str, Any]]] = {key: [] for key in categories}

    for row in results:
        category = classify_error_category(row.status_code, row.error_message, row.ok)
        if category is None:
            continue
        categories[category] += 1
        grouped_errors[category].append(
            {
                "target_url": row.target_url,
                "status_code": row.status_code,
                "error_message": row.error_message,
                "checked_at": row.checked_at,
                "source_pages": source_pages_by_target.get(row.target_url, []),
            }
        )

    distinct_sources: set[str] = set()
    for pages in source_pages_by_target.values():
        distinct_sources.update(pages)

    return {
        "meta": {
            "job_id": job_id,
            "base_url": base_url,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "crawl_run_id": crawl_run_id,
            "crawl_run_started_at": run_started_at,
            "crawl_run_finished_at": run_finished_at,
            "link_check_started_at": link_check_started_at,
            "link_check_finished_at": link_check_finished_at,
        },
        "summary": {
            "checked": checked,
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "skipped": skipped,
            "categories": categories,
        },
        "errors": grouped_errors,
        "provenance_stats": {
            "distinct_external_urls_checked": len(results),
            "distinct_source_pages_referenced": len(distinct_sources),
        },
    }


def write_link_check_report(path: Path, payload: dict[str, Any]) -> None:
    """Write report payload to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def report_filename(job_id: str, now: datetime | None = None) -> str:
    """Return report filename: report_[job]_yyyy-mm-dd_hh-mm.json."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M")
    return f"report_{job_id}_{stamp}.json"

