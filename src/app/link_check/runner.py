"""Link-check orchestration for discovered crawl links."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from app.link_check.http_client import HttpCheckResult
from app.models.job_config import JobConfig
from app.persistence.repository import CrawlRepository


class LinkChecker(Protocol):
    def check(self, url: str) -> HttpCheckResult:
        """Run one HTTP check."""


@dataclass(frozen=True)
class LinkCheckSummary:
    crawl_run_id: int | None
    checked: int
    passed: int
    failed: int
    errored: int
    skipped: int
    failure_samples: list[tuple[str, str]]


def run_link_check(
    config: JobConfig,
    repository: CrawlRepository,
    checker: LinkChecker,
    run_id: int | None = None,
    limit: int | None = None,
    status_hook: Callable[[str], None] | None = None,
    result_hook: Callable[[str, HttpCheckResult], None] | None = None,
) -> LinkCheckSummary:
    """Execute link checks for latest or selected crawl run."""
    if not config["link_check"]["enabled"]:
        return LinkCheckSummary(
            crawl_run_id=run_id,
            checked=0,
            passed=0,
            failed=0,
            errored=0,
            skipped=0,
            failure_samples=[],
        )

    crawl_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
    if crawl_run_id is None:
        return LinkCheckSummary(
            crawl_run_id=None,
            checked=0,
            passed=0,
            failed=0,
            errored=0,
            skipped=0,
            failure_samples=[],
        )

    links = repository.list_links_for_check(crawl_run_id=crawl_run_id, limit=limit)
    retries = config["link_check"]["retry_count"]

    checked = 0
    passed = 0
    failed = 0
    errored = 0
    failure_samples: list[tuple[str, str]] = []

    for link in links:
        if status_hook:
            status_hook(f"Checking: {link.target_url}")
        final_result: HttpCheckResult | None = None
        for _ in range(retries + 1):
            attempt_result = checker.check(link.target_url)
            final_result = attempt_result
            if attempt_result.ok:
                break

        if final_result is None:
            continue

        if result_hook:
            result_hook(link.target_url, final_result)

        checked += 1
        if final_result.ok:
            passed += 1
        elif final_result.status_code is None:
            errored += 1
            if len(failure_samples) < 5:
                failure_samples.append((link.target_url, final_result.error_message or "Unknown error"))
        else:
            failed += 1
            if len(failure_samples) < 5:
                failure_samples.append((link.target_url, final_result.error_message or f"HTTP {final_result.status_code}"))

        repository.add_link_check_result(
            crawl_link_id=link.link_id,
            crawl_run_id=link.crawl_run_id,
            target_url=link.target_url,
            status_code=final_result.status_code,
            ok=final_result.ok,
            error_message=final_result.error_message,
        )

    return LinkCheckSummary(
        crawl_run_id=crawl_run_id,
        checked=checked,
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=0,
        failure_samples=failure_samples,
    )
