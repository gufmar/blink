"""`blink crawl` command group."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from app.config.loader import load_effective_job_config
from app.config.schema import validate_job_config
from app.crawl.extractor import IGNORE_SECTION_KEYS
from app.crawl.runner import CrawlSummary, run_crawl
from app.notifications.models import NotificationMessage
from app.notifications.service import NotificationService
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.render.playwright_client import (
    BrowserSettings,
    BrowserViewport,
    ObservabilitySettings,
    PlaywrightRenderer,
    RenderError,
    RenderResult,
)
from app.runtime.job_paths import build_job_paths
from app.runtime.job_prepare import prepare_job_database, prepare_job_runtime
from app.runtime.logging import configure_logging, event_logger
from app.runtime.status import LiveStatus

crawl_app = typer.Typer(help="Run Blink crawl jobs.")


def _emit_runtime_notes(notes: list[str]) -> None:
    for line in notes:
        typer.secho(line, fg=typer.colors.CYAN)
        event_logger("runtime.job_layout").info(line)


def _emit_ignore_skip_report(summary: CrawlSummary) -> None:
    """Log and print per-section internal link skips (href phase)."""
    lines = [
        f"  {key}: {summary.ignore_internal_skipped[key]}"
        for key in IGNORE_SECTION_KEYS
    ]
    block = "Internal links skipped by ignore.* (would be internal):\n" + "\n".join(lines)
    typer.secho(block, fg=typer.colors.CYAN)
    event_logger("crawl.ignore_skipped", run_id=summary.run_id).info(block.replace("\n", " | "))


def _load_and_validate_job(job: str) -> dict:
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    issues = validate_job_config(config)
    if issues:
        typer.secho("Job config is invalid:", fg=typer.colors.RED, err=True)
        for issue in issues:
            typer.echo(f"- {issue.path}: {issue.message}", err=True)
        raise typer.Exit(code=1)
    return config


def _browser_settings_from_config(config: dict) -> BrowserSettings:
    browser_cfg = config["crawl"]["browser"]
    return BrowserSettings(
        user_agent=config["crawl"]["user_agent"],
        viewport=BrowserViewport(
            width=browser_cfg["viewport"]["width"],
            height=browser_cfg["viewport"]["height"],
        ),
        locale=browser_cfg["locale"],
        timezone_id=browser_cfg["timezone_id"],
        extra_http_headers=browser_cfg["extra_http_headers"],
        storage_state_path=browser_cfg["storage_state_path"],
        persist_storage_state=browser_cfg["persist_storage_state"],
        headless=browser_cfg["headless"],
        block_request_netloc_contains=browser_cfg["block_request_netloc_contains"],
    )


def _observability_from_config(config: dict) -> ObservabilitySettings:
    obs_cfg = config["crawl"]["observability"]
    return ObservabilitySettings(
        log_console=obs_cfg["log_console"],
        log_non_2xx_responses=obs_cfg["log_non_2xx_responses"],
        log_request_failures=obs_cfg["log_request_failures"],
        save_failure_screenshot=obs_cfg["save_failure_screenshot"],
        save_failure_html=obs_cfg["save_failure_html"],
    )


def _emit_page_diagnostics(result: RenderResult, depth: int, observability: ObservabilitySettings, run_id: int | str) -> None:
    event_logger("crawl.page_nav", run_id=run_id).debug(
        f"depth={depth} requested_url={result.requested_url} final_url={result.url}"
    )
    event_logger("crawl.page_response", run_id=run_id).debug(
        f"status={result.status_code} x-vercel-mitigated={result.response_headers.get('x-vercel-mitigated', '')} "
        f"x-vercel-id={result.response_headers.get('x-vercel-id', '')}"
    )
    if result.challenge_detected:
        event_logger("crawl.page_challenge_detected", run_id=run_id).warning(
            f"requested_url={result.requested_url} final_url={result.url} status={result.status_code}"
        )
    if observability.log_non_2xx_responses and (result.status_code is None or not (200 <= result.status_code < 300)):
        event_logger("crawl.page_non_2xx", run_id=run_id).warning(
            f"requested_url={result.requested_url} final_url={result.url} status={result.status_code}"
        )
    if observability.log_request_failures:
        for failure in result.request_failures:
            event_logger("crawl.page_request_failed", run_id=run_id).warning(failure)
    if observability.log_console:
        for line in result.console_messages:
            event_logger("crawl.page_console", run_id=run_id).debug(line)
    if result.screenshot_path:
        event_logger("crawl.page_artifact", run_id=run_id).info(f"screenshot={result.screenshot_path}")
    if result.html_snapshot_path:
        event_logger("crawl.page_artifact", run_id=run_id).info(f"html_snapshot={result.html_snapshot_path}")


def _send_crawl_summary_notification(config: dict, *, db_path: Path, summary: CrawlSummary) -> None:
    notifications = config["notifications"]
    if not notifications["enabled"] or not notifications["crawl_summary_on_run"]:
        return
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        prev_run_id = repository.get_previous_run_id(config["meta"]["job_id"], summary.run_id)
        prev = repository.list_run_history(config["meta"]["job_id"], limit=2)
    finally:
        connection.close()
    previous = prev[1] if len(prev) > 1 else None

    def delta(current: int, old: int | None) -> str:
        if old is None:
            return "n/a"
        return f"{current - old:+d}"

    body = (
        f"job_id={config['meta']['job_id']}\n"
        f"run_id={summary.run_id}\n"
        f"pages_visited={summary.pages_visited} (delta {delta(summary.pages_visited, previous.pages_visited if previous else None)})\n"
        f"pages_failed={summary.pages_failed} (delta {delta(summary.pages_failed, previous.pages_failed if previous else None)})\n"
        f"links_discovered={summary.links_discovered} (delta {delta(summary.links_discovered, previous.links_discovered if previous else None)})\n"
        f"ignored_internal_links={sum(summary.ignore_internal_skipped.values())}\n"
        f"external_unique={summary.unique_external_urls}\n"
        f"previous_run_id={prev_run_id if prev_run_id is not None else '-'}"
    )
    message = NotificationMessage(
        job_id=config["meta"]["job_id"],
        title=f"Blink crawl summary ({config['meta']['job_id']})",
        body=body,
    )
    service = NotificationService()
    dispatches = service.send_message(notifications, message)
    for dispatch in dispatches:
        if dispatch.success:
            event_logger("notifications.crawl_summary_sent", run_id=summary.run_id).info(
                f"provider={dispatch.provider} destination={dispatch.destination_id}"
            )
        else:
            event_logger("notifications.crawl_summary_failed", run_id=summary.run_id).warning(
                f"provider={dispatch.provider} destination={dispatch.destination_id} error={dispatch.error or 'unknown'}"
            )


@crawl_app.command("run")
def run(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    max_pages: int | None = typer.Option(None, "--max-pages", help="Optional max pages override."),
    debug: bool = typer.Option(False, "--debug", help="Enable verbose console logging."),
) -> None:
    """Run a crawl using validated job config."""
    config = _load_and_validate_job(job)

    paths = build_job_paths(config["meta"]["job_id"])
    if db:
        db_path = db.resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_notes = prepare_job_database(db_path)
    else:
        db_path = paths.db_path
        runtime_notes = prepare_job_runtime(paths)

    with LiveStatus(enabled=True) as status:
        configure_logging(log_file=paths.log_path, console=status.console, debug=debug)
        _emit_runtime_notes(runtime_notes)
        status.update("Preparing crawl runtime")
        event_logger("crawl.command_start").info(f"job_id={config['meta']['job_id']} db={db_path}")

        connection = connect_sqlite(db_path)
        initialize_schema(connection)
        repository = CrawlRepository(connection)
        observability = _observability_from_config(config)
        renderer = PlaywrightRenderer(
            navigation_timeout_seconds=config["crawl"]["timeouts"]["navigation_seconds"],
            network_idle_seconds=config["crawl"]["timeouts"]["network_idle_seconds"],
            playwright_wait_seconds=config["crawl"]["timeouts"]["playwright_wait_seconds"],
            browser_settings=_browser_settings_from_config(config),
            observability=observability,
            artifacts_dir=paths.artifacts_dir,
        )

        try:
            summary = run_crawl(
                config=config,
                repository=repository,
                renderer=renderer,
                max_pages_override=max_pages,
                status_hook=status.update,
                diagnostics_hook=lambda result, depth, run_id: _emit_page_diagnostics(result, depth, observability, run_id),
            )
        except RenderError as exc:
            event_logger("crawl.runtime_error").error(str(exc))
            typer.secho(f"Crawl failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        finally:
            connection.close()
            status.update("Crawl finished")

    typer.secho(
        (
            f"Crawl run complete: run_id={summary.run_id}, "
            f"pages_visited={summary.pages_visited}, "
            f"pages_failed={summary.pages_failed}, "
            f"external_unique={summary.unique_external_urls}, "
            f"link_rows={summary.links_discovered}, "
            f"challenged={summary.challenged_pages}, "
            f"non_2xx={summary.non_2xx_pages}, "
            f"request_failures={summary.request_failures}"
        ),
        fg=typer.colors.GREEN,
    )
    event_logger("crawl.summary", run_id=summary.run_id).info(
        (
            f"pages_visited={summary.pages_visited} "
            f"pages_failed={summary.pages_failed} "
            f"external_unique={summary.unique_external_urls} "
            f"link_rows={summary.links_discovered} "
            f"challenged={summary.challenged_pages} "
            f"non_2xx={summary.non_2xx_pages} "
            f"request_failures={summary.request_failures}"
        )
    )
    _emit_ignore_skip_report(summary)
    if summary.failed_pages:
        typer.secho("Page failures:", fg=typer.colors.YELLOW, err=True)
        for failed_url, message in summary.failed_pages:
            typer.echo(f"- {failed_url}: {message}", err=True)
            event_logger("crawl.page_failed", run_id=summary.run_id).warning(f"url={failed_url} error={message}")
    _send_crawl_summary_notification(config, db_path=db_path, summary=summary)


@crawl_app.command("explore")
def explore(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    max_pages: int = typer.Option(0, "--max-pages", help="0 means no explicit page limit."),
    max_runtime_minutes: int = typer.Option(15, "--max-runtime-minutes", min=1, help="Guardrail runtime cap."),
    progress_every: int = typer.Option(25, "--progress-every", min=1, help="Emit progress every N pages."),
    debug: bool = typer.Option(False, "--debug", help="Enable verbose console logging."),
) -> None:
    """Run deep crawl exploration to measure recursion and data growth."""
    config = _load_and_validate_job(job)
    paths = build_job_paths(config["meta"]["job_id"])
    if db:
        db_path = db.resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_notes = prepare_job_database(db_path)
    else:
        db_path = paths.db_path
        runtime_notes = prepare_job_runtime(paths)

    with LiveStatus(enabled=True) as status:
        configure_logging(log_file=paths.log_path, console=status.console, debug=debug)
        _emit_runtime_notes(runtime_notes)
        status.update("Preparing crawl exploration")
        event_logger("crawl.explore_start").info(
            (
                f"job_id={config['meta']['job_id']} db={db_path} "
                f"max_pages={max_pages} max_runtime_minutes={max_runtime_minutes}"
            )
        )
        db_size_before = db_path.stat().st_size if db_path.exists() else 0
        started = time.monotonic()

        connection = connect_sqlite(db_path)
        initialize_schema(connection)
        repository = CrawlRepository(connection)
        observability = _observability_from_config(config)
        renderer = PlaywrightRenderer(
            navigation_timeout_seconds=config["crawl"]["timeouts"]["navigation_seconds"],
            network_idle_seconds=config["crawl"]["timeouts"]["network_idle_seconds"],
            playwright_wait_seconds=config["crawl"]["timeouts"]["playwright_wait_seconds"],
            browser_settings=_browser_settings_from_config(config),
            observability=observability,
            artifacts_dir=paths.artifacts_dir,
        )

        def on_progress(
            pages_visited: int,
            link_rows: int,
            unique_external: int,
            frontier_size: int,
        ) -> None:
            if pages_visited > 0 and pages_visited % progress_every == 0:
                elapsed = time.monotonic() - started
                size_bytes = db_path.stat().st_size if db_path.exists() else 0
                status.update(
                    f"Exploring pages={pages_visited} queue~{frontier_size} "
                    f"external_unique={unique_external} link_rows={link_rows}"
                )
                event_logger("crawl.explore_progress").info(
                    (
                        f"pages_visited={pages_visited} external_unique={unique_external} "
                        f"link_rows={link_rows} queue_size={frontier_size} "
                        f"elapsed_seconds={elapsed:.1f} db_bytes={size_bytes}"
                    )
                )

        try:
            summary = run_crawl(
                config=config,
                repository=repository,
                renderer=renderer,
                max_pages_override=max_pages,
                status_hook=status.update,
                progress_hook=on_progress,
                diagnostics_hook=lambda result, depth, run_id: _emit_page_diagnostics(result, depth, observability, run_id),
                max_runtime_seconds=max_runtime_minutes * 60,
            )
        except RenderError as exc:
            event_logger("crawl.explore_error").error(str(exc))
            typer.secho(f"Crawl exploration failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        finally:
            connection.close()
            status.update("Crawl exploration finished")

    db_size_after = db_path.stat().st_size if db_path.exists() else 0
    growth = db_size_after - db_size_before
    elapsed = time.monotonic() - started
    typer.secho(
        (
            f"Crawl explore complete: run_id={summary.run_id}, pages_visited={summary.pages_visited}, "
            f"pages_failed={summary.pages_failed}, external_unique={summary.unique_external_urls}, "
            f"link_rows={summary.links_discovered}, challenged={summary.challenged_pages}, "
            f"non_2xx={summary.non_2xx_pages}, request_failures={summary.request_failures}, "
            f"elapsed_seconds={elapsed:.1f}, db_growth_bytes={growth}"
        ),
        fg=typer.colors.GREEN,
    )
    event_logger("crawl.explore_summary", run_id=summary.run_id).info(
        (
            f"pages_visited={summary.pages_visited} pages_failed={summary.pages_failed} "
            f"external_unique={summary.unique_external_urls} link_rows={summary.links_discovered} "
            f"challenged={summary.challenged_pages} non_2xx={summary.non_2xx_pages} "
            f"request_failures={summary.request_failures} "
            f"elapsed_seconds={elapsed:.1f} db_growth_bytes={growth}"
        )
    )
    _emit_ignore_skip_report(summary)
