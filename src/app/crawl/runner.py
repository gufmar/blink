"""Crawl runner orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

from app.crawl.extractor import (
    IGNORE_SECTION_KEYS,
    extract_links,
    empty_ignore_skip_counts,
)
from app.crawl.main_text import extract_page_main_text
from app.crawl.frontier import CrawlFrontier
from app.models.job_config import JobConfig
from app.persistence.repository import CrawlRepository
from app.render.playwright_client import RenderError, RenderResult


class Renderer(Protocol):
    def open_session(self) -> None:
        """Initialize browser session for this run."""

    def render(self, url: str) -> RenderResult:
        """Render URL and return html payload."""

    def close_session(self) -> None:
        """Close renderer session."""


@dataclass(frozen=True)
class CrawlSummary:
    run_id: int
    pages_visited: int
    pages_failed: int
    links_discovered: int
    unique_external_urls: int
    challenged_pages: int
    non_2xx_pages: int
    request_failures: int
    ignore_internal_skipped: dict[str, int]
    failed_pages: list[tuple[str, str]]


def _seed_urls(config: JobConfig) -> list[str]:
    start_urls = config["target"]["start_urls"]
    if start_urls:
        return start_urls
    return [config["target"]["base_url"]]


def run_crawl(
    config: JobConfig,
    repository: CrawlRepository,
    renderer: Renderer,
    max_pages_override: int | None = None,
    status_hook: Callable[[str], None] | None = None,
    progress_hook: Callable[[int, int, int, int], None] | None = None,
    diagnostics_hook: Callable[[RenderResult, int, int], None] | None = None,
    max_runtime_seconds: int | None = None,
) -> CrawlSummary:
    """Run a bounded crawl loop based on validated job config."""
    max_pages_from_job = config["crawl"]["max_pages_per_run"]
    if max_pages_override is not None:
        max_pages = 10**9 if max_pages_override <= 0 else max_pages_override
    elif max_pages_from_job > 0:
        max_pages = max_pages_from_job
    else:
        max_pages = 100  # safety cap for MVP

    max_depth = config["crawl"]["max_depth"]
    delay = config["crawl"]["request_delay_seconds"]
    retries = config["crawl"]["retry_count"]

    run_id = repository.create_run(config["meta"]["job_id"])
    frontier = CrawlFrontier()
    for seed in _seed_urls(config):
        frontier.enqueue(seed, depth=0)

    pages_visited = 0
    pages_failed = 0
    links_discovered = 0
    unique_external: set[str] = set()
    challenged_pages = 0
    non_2xx_pages = 0
    request_failures_count = 0
    ignore_skipped = empty_ignore_skip_counts()
    failed_pages: list[tuple[str, str]] = []
    started = time.monotonic()

    renderer.open_session()
    try:
        while len(frontier) > 0 and pages_visited < max_pages:
            if max_runtime_seconds is not None and time.monotonic() - started >= max_runtime_seconds:
                break
            item = frontier.pop()
            if item is None:
                break
            if max_depth is not None and item.depth > max_depth:
                continue

            if delay > 0:
                time.sleep(delay)

            if status_hook:
                status_hook(f"Crawling depth={item.depth}: {item.url}")

            last_error: str | None = None
            render_result: RenderResult | None = None
            for _ in range(retries + 1):
                try:
                    render_result = renderer.render(item.url)
                    last_error = None
                    break
                except RenderError as exc:
                    last_error = str(exc)

            if render_result is None:
                pages_failed += 1
                pages_visited += 1
                failure_message = last_error or "Unknown render failure"
                repository.add_page_result(
                    run_id=run_id,
                    url=item.url,
                    depth=item.depth,
                    status_code=None,
                    ok=False,
                    error_message=failure_message,
                )
                failed_pages.append((item.url, failure_message))
                if progress_hook:
                    progress_hook(pages_visited, links_discovered, len(unique_external), len(frontier))
                continue

            if render_result.challenge_detected:
                challenged_pages += 1
            if render_result.status_code is None or not (200 <= render_result.status_code < 300):
                non_2xx_pages += 1
            request_failures_count += len(render_result.request_failures)
            if diagnostics_hook:
                diagnostics_hook(render_result, item.depth, run_id)

            main_text = (
                extract_page_main_text(
                    render_result.html,
                    config["content"]["main_text_extractor"],
                )
                if config["content"]["extract_main_text"]
                else None
            )
            stored_html = render_result.html if config["content"]["store_rendered_html"] else None
            repository.add_page_result(
                run_id=run_id,
                url=render_result.url,
                depth=item.depth,
                status_code=render_result.status_code,
                ok=True,
                html=stored_html,
                main_text=main_text,
            )
            repository.prune_page_history(render_result.url, config["content"]["history_keep"])

            extracted = extract_links(render_result.url, render_result.html, config)
            for key in IGNORE_SECTION_KEYS:
                ignore_skipped[key] += extracted.internal_skipped_by_reason[key]
            for url, is_internal in extracted.links:
                links_discovered += 1
                if not is_internal:
                    unique_external.add(url)
                repository.add_link(
                    run_id=run_id,
                    source_url=render_result.url,
                    target_url=url,
                    is_internal=is_internal,
                )
                if is_internal and (max_depth is None or item.depth < max_depth):
                    frontier.enqueue(url, depth=item.depth + 1)

            pages_visited += 1
            if progress_hook:
                progress_hook(pages_visited, links_discovered, len(unique_external), len(frontier))
    finally:
        renderer.close_session()

    repository.finish_run(
        run_id=run_id,
        pages_visited=pages_visited,
        pages_failed=pages_failed,
        links_discovered=links_discovered,
    )
    repository.compute_run_diffs(job_id=config["meta"]["job_id"], run_id=run_id)
    repository.compute_page_text_metrics(
        job_id=config["meta"]["job_id"],
        run_id=run_id,
        significant_change_threshold_percent=config["content"]["significant_change_threshold_percent"],
        text_compare_max_chars=config["content"]["text_compare_max_chars"],
    )
    return CrawlSummary(
        run_id=run_id,
        pages_visited=pages_visited,
        pages_failed=pages_failed,
        links_discovered=links_discovered,
        unique_external_urls=len(unique_external),
        challenged_pages=challenged_pages,
        non_2xx_pages=non_2xx_pages,
        request_failures=request_failures_count,
        ignore_internal_skipped=dict(ignore_skipped),
        failed_pages=failed_pages,
    )
