"""`blink link-check` command group."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config.loader import load_effective_job_config
from app.config.schema import validate_job_config
from app.link_check.http_client import HttpCheckResult, HttpLinkChecker
from app.link_check.reporting import build_link_check_report, report_filename, write_link_check_report
from app.link_check.runner import run_link_check
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.job_paths import build_job_paths
from app.runtime.job_prepare import prepare_job_database, prepare_job_runtime
from app.runtime.logging import configure_logging, event_logger
from app.runtime.status import LiveStatus

link_check_app = typer.Typer(help="Run Blink link checks.")
_console = Console()


def _emit_runtime_notes(notes: list[str]) -> None:
    for line in notes:
        typer.secho(line, fg=typer.colors.CYAN)
        event_logger("runtime.job_layout").info(line)


def _render_rows_table(rows: list[dict[str, object]]) -> None:
    if not rows:
        typer.echo("No rows found.")
        return
    columns = list(rows[0].keys())
    table = Table(show_header=True, header_style="bold")
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*[str(row.get(col, "")) if row.get(col) is not None else "" for col in columns])
    _console.print(table)


def _emit_live_failure(url: str, result: HttpCheckResult, run_id: int | str = "-") -> None:
    if result.ok:
        return
    reason = result.error_message or (f"HTTP {result.status_code}" if result.status_code is not None else "Unknown error")
    typer.secho(f"link-check failed: {url} -> {reason}", fg=typer.colors.YELLOW, err=True)
    event_logger("linkcheck.failure", run_id=run_id).warning(f"url={url} reason={reason}")


@link_check_app.command("show")
def show(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Crawl run id; defaults to latest run."),
    only_failed: bool = typer.Option(False, "--only-failed", help="Show only failed/errored checks."),
    search: str | None = typer.Option(None, "--search", help="Substring match on target URL."),
    limit: int = typer.Option(200, "--limit", min=1, help="Max rows to show."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show latest link-check result per target URL for a crawl run."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
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

    paths = build_job_paths(config["meta"]["job_id"])
    db_path = db.resolve() if db else paths.db_path
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return
        records = repository.list_latest_link_check_results(selected_run_id)
        if search:
            records = [row for row in records if search.lower() in row.target_url.lower()]
        if only_failed:
            records = [row for row in records if not row.ok]
        records = records[:limit]
        source_pages = repository.list_source_pages_for_targets(
            selected_run_id,
            [row.target_url for row in records],
        )
    finally:
        connection.close()

    payload = [
        {
            "run_id": record.crawl_run_id,
            "target_url": record.target_url,
            "status_code": record.status_code,
            "ok": record.ok,
            "error_message": record.error_message or "",
            "checked_at": record.checked_at,
            "source_pages": "; ".join(source_pages.get(record.target_url, [])),
        }
        for record in records
    ]
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    _render_rows_table(payload)


@link_check_app.command("run")
def run(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Optional crawl run id to check."),
    limit: int | None = typer.Option(None, "--limit", help="Optional max number of links to check."),
    debug: bool = typer.Option(False, "--debug", help="Enable verbose console logging."),
) -> None:
    """Run link checks against discovered links."""
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

    if not config["link_check"]["enabled"]:
        typer.secho("Link-check is disabled in job config. Skipping.", fg=typer.colors.YELLOW)
        return

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
        event_logger("linkcheck.command_start").info(f"job_id={config['meta']['job_id']} db={db_path}")
        status.update("Preparing link-check runtime")

        connection = connect_sqlite(db_path)
        initialize_schema(connection)
        repository = CrawlRepository(connection)
        checker = HttpLinkChecker(
            timeout_seconds=config["link_check"]["request_timeout_seconds"],
            follow_redirects=config["link_check"]["follow_redirects"],
        )
        link_check_started_at = datetime.now(tz=UTC).isoformat()
        try:
            summary = run_link_check(
                config=config,
                repository=repository,
                checker=checker,
                run_id=run_id,
                limit=limit,
                status_hook=status.update,
                result_hook=lambda url, result: _emit_live_failure(url, result, run_id=run_id or "-"),
            )
            link_check_finished_at = datetime.now(tz=UTC).isoformat()
        finally:
            connection.close()
            status.update("Link-check finished")

    if summary.crawl_run_id is None:
        typer.secho("No crawl run found for this job. Run `blink crawl run` first.", fg=typer.colors.YELLOW)
        event_logger("linkcheck.no_crawl_run").warning("No crawl run found for this job.")
        return

    typer.secho(
        (
            f"Link-check complete: crawl_run_id={summary.crawl_run_id}, "
            f"checked={summary.checked}, passed={summary.passed}, "
            f"failed={summary.failed}, errored={summary.errored}, skipped={summary.skipped}"
        ),
        fg=typer.colors.GREEN,
    )
    event_logger("linkcheck.summary", run_id=summary.crawl_run_id).info(
        (
            f"checked={summary.checked} passed={summary.passed} "
            f"failed={summary.failed} errored={summary.errored} skipped={summary.skipped}"
        )
    )
    if summary.failure_samples:
        typer.secho("Failure samples:", fg=typer.colors.YELLOW, err=True)
        for failed_url, message in summary.failure_samples:
            typer.echo(f"- {failed_url}: {message}", err=True)
            event_logger("linkcheck.failure_sample", run_id=summary.crawl_run_id).warning(
                f"url={failed_url} error={message}"
            )

    if config["link_check"]["write_json_report"]:
        report_path = paths.reports_dir / report_filename(config["meta"]["job_id"])
        report_connection = connect_sqlite(db_path)
        initialize_schema(report_connection)
        report_repo = CrawlRepository(report_connection)
        try:
            run_started_at, run_finished_at = report_repo.get_run_started_finished(summary.crawl_run_id)
            latest_results = report_repo.list_latest_link_check_results(summary.crawl_run_id)
            source_pages = report_repo.list_source_pages_for_targets(
                summary.crawl_run_id,
                [result.target_url for result in latest_results],
            )
        finally:
            report_connection.close()
        payload = build_link_check_report(
            job_id=config["meta"]["job_id"],
            base_url=config["target"]["base_url"],
            crawl_run_id=summary.crawl_run_id,
            run_started_at=run_started_at,
            run_finished_at=run_finished_at,
            link_check_started_at=link_check_started_at,
            link_check_finished_at=link_check_finished_at,
            checked=summary.checked,
            passed=summary.passed,
            failed=summary.failed,
            errored=summary.errored,
            skipped=summary.skipped,
            results=latest_results,
            source_pages_by_target=source_pages,
        )
        write_link_check_report(report_path, payload)
        typer.secho(f"Link-check JSON report written: {report_path}", fg=typer.colors.CYAN)
        event_logger("linkcheck.report_written", run_id=summary.crawl_run_id).info(f"path={report_path}")
