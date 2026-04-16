"""Link-check orchestration for discovered crawl links."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol

from app.link_check.http_client import HttpCheckResult
from app.link_check.reporting import classify_error_category
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
    ignored: int
    pending_tolerance: int
    reportable_failures: int
    failure_samples: list[tuple[str, str]]


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_reportable_failure(
    *,
    state_first_failed_at: str,
    consecutive_failures: int,
    min_consecutive_failures: int,
    min_age_days: int,
    now: datetime,
) -> bool:
    elapsed_days = (now - _parse_timestamp(state_first_failed_at)).total_seconds() / 86400.0
    reached_consecutive = consecutive_failures >= min_consecutive_failures
    reached_age = elapsed_days >= float(min_age_days)
    # Step 9 decision: report when either threshold is met.
    return reached_consecutive or reached_age


def run_link_check(
    config: JobConfig,
    repository: CrawlRepository,
    checker: LinkChecker,
    run_id: int | None = None,
    limit: int | None = None,
    max_reportable_failures: int | None = None,
    preexisting_reportable_targets: set[str] | None = None,
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
            ignored=0,
            pending_tolerance=0,
            reportable_failures=0,
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
            ignored=0,
            pending_tolerance=0,
            reportable_failures=0,
            failure_samples=[],
        )

    links = repository.list_links_for_check(crawl_run_id=crawl_run_id, limit=limit)
    retries = config["link_check"]["retry_count"]

    checked = 0
    passed = 0
    failed = 0
    errored = 0
    ignored = 0
    pending_tolerance = 0
    reportable_failures = 0
    newly_reportable_failures = 0
    failure_samples: list[tuple[str, str]] = []
    known_reportables = set(preexisting_reportable_targets or set())

    total_links = len(links)
    for index, link in enumerate(links, start=1):
        if status_hook:
            progress_pct = int(((index - 1) / total_links) * 100) if total_links > 0 else 100
            status_hook(f"Checking progress={progress_pct}% ({index}/{total_links}): {link.target_url}")
        final_result: HttpCheckResult | None = None
        for _ in range(retries + 1):
            attempt_result = checker.check(link.target_url)
            final_result = attempt_result
            if attempt_result.ok:
                break

        if final_result is None:
            continue

        checked += 1
        now = datetime.now(tz=UTC)
        if final_result.ok:
            passed += 1
            repository.clear_link_failure_state(
                job_id=config["meta"]["job_id"],
                target_url=link.target_url,
            )
            decision_state = "ok"
            error_category = None
            ignore_rule_id = None
            decision_reason = None
        else:
            error_category = classify_error_category(final_result.status_code, final_result.error_message, final_result.ok)
            if final_result.status_code is None:
                errored += 1
            else:
                failed += 1
            ignored_http_statuses = set(config["link_check"]["http_status"])
            if final_result.status_code is not None and final_result.status_code in ignored_http_statuses:
                decision_state = "ignored"
                ignore_rule_id = None
                decision_reason = f"link_check.http_status:{final_result.status_code}"
                ignored += 1
            else:
                ignore_rule = repository.find_matching_link_ignore_rule(
                    job_id=config["meta"]["job_id"],
                    target_url=link.target_url,
                    now=now,
                )

                if ignore_rule is not None:
                    decision_state = "ignored"
                    ignore_rule_id = ignore_rule.rule_id
                    decision_reason = (
                        f"ignore-rule:{ignore_rule.rule_id} ({ignore_rule.match_type}={ignore_rule.pattern})"
                    )
                    ignored += 1
                else:
                    ignore_rule_id = None
                    tolerance = config["link_check"]["tolerance"]["by_category"]
                    category = error_category or "other"
                    category_rule = tolerance[category]
                    state = repository.record_link_failure_state(
                        job_id=config["meta"]["job_id"],
                        target_url=link.target_url,
                        error_category=category,
                        status_code=final_result.status_code,
                        error_message=final_result.error_message,
                        failed_at=now,
                    )
                    is_reportable = _is_reportable_failure(
                        state_first_failed_at=state.first_failed_at,
                        consecutive_failures=state.consecutive_failures,
                        min_consecutive_failures=category_rule["min_consecutive_failures"],
                        min_age_days=category_rule["min_age_days"],
                        now=now,
                    )
                    if is_reportable:
                        decision_state = "reportable"
                        decision_reason = (
                            f"tolerance-met: consecutive={state.consecutive_failures}/"
                            f"{category_rule['min_consecutive_failures']} "
                            f"or min_age_days={category_rule['min_age_days']}"
                        )
                        reportable_failures += 1
                        if len(failure_samples) < 5:
                            failure_samples.append(
                                (
                                    link.target_url,
                                    final_result.error_message or f"HTTP {final_result.status_code}",
                                )
                            )
                        if result_hook:
                            result_hook(link.target_url, final_result)
                        if link.target_url not in known_reportables:
                            known_reportables.add(link.target_url)
                            newly_reportable_failures += 1
                    else:
                        decision_state = "pending_tolerance"
                        decision_reason = (
                            f"tolerance-pending: consecutive={state.consecutive_failures}/"
                            f"{category_rule['min_consecutive_failures']} "
                            f"or min_age_days={category_rule['min_age_days']}"
                        )
                        pending_tolerance += 1

        result_row_id = repository.add_link_check_result(
            crawl_link_id=link.link_id,
            crawl_run_id=link.crawl_run_id,
            target_url=link.target_url,
            status_code=final_result.status_code,
            ok=final_result.ok,
            error_message=final_result.error_message,
            error_category=error_category,
            decision_state=decision_state,
            ignore_rule_id=ignore_rule_id,
            decision_reason=decision_reason,
        )
        if final_result.screenshot_file:
            repository.add_link_check_screenshot(
                link_check_result_id=result_row_id,
                crawl_run_id=link.crawl_run_id,
                target_url=link.target_url,
                status_code=final_result.status_code,
                error_message=final_result.error_message,
                artifact_file=final_result.screenshot_file,
            )

        if (
            max_reportable_failures is not None
            and max_reportable_failures > 0
            and newly_reportable_failures >= max_reportable_failures
        ):
            break

    skipped = ignored + pending_tolerance
    return LinkCheckSummary(
        crawl_run_id=crawl_run_id,
        checked=checked,
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        ignored=ignored,
        pending_tolerance=pending_tolerance,
        reportable_failures=reportable_failures,
        failure_samples=failure_samples,
    )
