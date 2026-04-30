"""Build and write JSON link-check reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.persistence.repository import LinkCheckResultRecord

_TIMEOUT_TOKENS = ("timed out", "timeout", "err_timed_out")
_DNS_TOKENS = (
    "name or service not known",
    "temporary failure in name resolution",
    "dns",
    "nodename nor servname provided",
    "err_name_not_resolved",
)
_CONNECTION_TOKENS = (
    "connection refused",
    "failed to establish a new connection",
    "network is unreachable",
    "connection reset",
    "ssl",
    "certificate",
    "too many redirects",
    "dns",
    "urlopen error",
    "err_connection_refused",
    "err_connection_reset",
    "err_internet_disconnected",
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
    if any(token in message for token in _DNS_TOKENS):
        return "client"
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
    ignored: int,
    pending_tolerance: int,
    reportable_failures: int,
    results: list[LinkCheckResultRecord],
    source_refs_by_target: dict[str, list[dict[str, str | None]]],
    screenshot_by_result_id: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Build a v3 JSON report payload for one link-check run."""
    screenshot_by_result_id = screenshot_by_result_id or {}
    categories: dict[str, int] = {"client": 0, "server": 0, "timeout": 0, "connection": 0, "other": 0}
    grouped_errors: dict[str, list[dict[str, Any]]] = {key: [] for key in categories}
    suppressed: dict[str, list[dict[str, Any]]] = {"ignored": [], "pending_tolerance": []}

    for row in results:
        decision_state = row.decision_state
        if decision_state in {"ignored", "pending_tolerance"}:
            suppressed[decision_state].append(
                {
                    "checked_at": row.checked_at,
                    "target_url": row.target_url,
                    "status_code": row.status_code,
                    "error_message": row.error_message,
                    "blinks": source_refs_by_target.get(row.target_url, []),
                    "screenshot_id": screenshot_by_result_id.get(row.row_id),
                    "error_category": row.error_category,
                    "decision_reason": row.decision_reason,
                }
            )
            continue
        category = classify_error_category(row.status_code, row.error_message, row.ok)
        if category is None:
            continue
        categories[category] += 1
        grouped_errors[category].append(
            {
                "checked_at": row.checked_at,
                "target_url": row.target_url,
                "status_code": row.status_code,
                "error_message": row.error_message,
                "blinks": source_refs_by_target.get(row.target_url, []),
                "screenshot_id": screenshot_by_result_id.get(row.row_id),
            }
        )

    distinct_sources: set[str] = set()
    for refs in source_refs_by_target.values():
        distinct_sources.update(ref["page_url"] for ref in refs)

    return {
        "job": {
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
            "provenance_stats": {
                "distinct_external_urls_checked": len(results),
                "distinct_source_pages_referenced": len(distinct_sources),
            },
            "suppressed": suppressed,
            "summary": {
                "checked": checked,
                "passed": passed,
                "failed": failed,
                "errored": errored,
                "skipped": skipped,
                "ignored": ignored,
                "pending_tolerance": pending_tolerance,
                "reportable_failures": reportable_failures,
                "categories": categories,
            },
        },
        "errors": grouped_errors,
    }


def write_link_check_report(path: Path, payload: dict[str, Any]) -> None:
    """Write report payload to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def report_filename(job_id: str, now: datetime | None = None) -> str:
    """Return report filename: report_[job]_yyyy-mm-dd_hh-mm.json."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M")
    return f"report_{job_id}_{stamp}.json"

