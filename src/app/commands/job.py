"""`blink jobs` command group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from app.config.loader import load_effective_job_config
from app.config.schema import validate_job_config
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.formatting import format_bytes_human
from app.runtime.job_paths import build_job_paths

jobs_app = typer.Typer(help="Inspect and validate Blink job configs.")
_console = Console()

_PAGES_SORT = frozenset({"created_at", "url", "depth", "status_code", "run_id"})
_EXTERNAL_SORT = frozenset({"seen_count", "target_url", "url", "first_seen_at", "created_at"})


def _resolve_db_path(config: dict[str, Any], db: Path | None) -> Path:
    if db:
        return db.resolve()
    return build_job_paths(config["meta"]["job_id"]).db_path


def _render_table(columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    table = Table(show_header=True, header_style="bold")
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*[str(cell) if cell is not None else "" for cell in row])
    _console.print(table)


def _print_items(items: list[dict[str, Any]], output_format: str) -> None:
    if output_format == "json":
        typer.echo(json.dumps(items, indent=2, sort_keys=True))
        return
    if not items:
        typer.echo("No rows found.")
        return
    columns = list(items[0].keys())
    rows = [tuple(item[column] for column in columns) for item in items]
    _render_table(columns, rows)


def _print_numbered_items(items: list[dict[str, Any]], output_format: str) -> None:
    if output_format == "json":
        numbered = [{"#": index, **row} for index, row in enumerate(items, start=1)]
        typer.echo(json.dumps(numbered, indent=2, sort_keys=True))
        return
    if not items:
        typer.echo("No rows found.")
        return
    keys = list(items[0].keys())
    columns = ["#", *keys]
    rows: list[tuple[str, ...]] = []
    for index, row in enumerate(items, start=1):
        rows.append((str(index), *[str(row[key]) for key in keys]))
    _render_table(columns, rows)


def _print_db_stats(rows: list[dict[str, Any]], output_format: str) -> None:
    if output_format == "json":
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        typer.echo("No stats.")
        return
    columns = list(rows[0].keys())
    table_rows = [tuple(str(row[column]) if row[column] is not None else "" for column in columns) for row in rows]
    _render_table(columns, table_rows)


@jobs_app.command("validate")
def validate(job: str = typer.Option(..., "--job", help="Path to job JSON file.")) -> None:
    """Validate a job config against schema."""
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

    typer.secho("Job config is valid.", fg=typer.colors.GREEN)


@jobs_app.command("show")
def show(job: str = typer.Option(..., "--job", help="Path to job JSON file.")) -> None:
    """Print the effective merged job config."""
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo(json.dumps(config, indent=2, sort_keys=True))


@jobs_app.command("history")
def history(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    limit: int = typer.Option(20, "--limit", min=1, help="Max runs to show."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show recent crawl run history and key metrics."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        records = repository.list_run_history(config["meta"]["job_id"], limit=limit)
    finally:
        connection.close()
    items = [
        {
            "run_id": record.run_id,
            "started_at": record.started_at,
            "finished_at": record.finished_at or "",
            "pages_visited": record.pages_visited,
            "pages_failed": record.pages_failed,
            "links_discovered": record.links_discovered,
        }
        for record in records
    ]
    _print_items(items, output_format=output_format)


@jobs_app.command("pages")
def pages(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Optional crawl run id."),
    search: str | None = typer.Option(None, "--search", "--search-by", help="Substring match on URL."),
    limit: int = typer.Option(100, "--limit", min=1, help="Max rows to show."),
    only_failed: bool = typer.Option(False, "--only-failed", help="Show only failed crawled pages."),
    max_depth: int | None = typer.Option(None, "--max-depth", help="Only pages with depth <= this value."),
    status_code: int | None = typer.Option(None, "--status-code", help="Filter by HTTP status code."),
    sort_by: str = typer.Option("created_at", "--sort-by", help="Sort column."),
    sort_order: str = typer.Option("desc", "--sort-order", help="asc or desc."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show crawled page URLs with optional search filter."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if sort_by not in _PAGES_SORT:
        typer.secho(f"Invalid --sort-by. Choose one of: {', '.join(sorted(_PAGES_SORT))}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if sort_order not in {"asc", "desc"}:
        typer.secho("Invalid --sort-order. Use asc or desc.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return
        records = repository.list_crawled_pages(
            run_id=selected_run_id,
            search=search,
            limit=limit,
            only_failed=only_failed,
            max_depth=max_depth,
            status_code_filter=status_code,
            sort_by=sort_by,
            sort_desc=sort_order == "desc",
        )
    finally:
        connection.close()
    items = [
        {
            "run_id": record.run_id,
            "url": record.url,
            "depth": record.depth,
            "status_code": record.status_code,
            "ok": record.ok,
            "created_at": record.created_at,
            "error_message": record.error_message or "",
        }
        for record in records
    ]
    _print_numbered_items(items, output_format=output_format)


@jobs_app.command("external-links")
def external_links(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Optional crawl run id."),
    search: str | None = typer.Option(None, "--search", "--search-by", help="Substring match on URL."),
    limit: int = typer.Option(100, "--limit", min=1, help="Max rows to show."),
    sort_by: str = typer.Option("seen_count", "--sort-by", help="Sort column."),
    sort_order: str = typer.Option("desc", "--sort-order", help="asc or desc."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show distinct external URLs discovered by crawl."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if sort_by not in _EXTERNAL_SORT:
        typer.secho(f"Invalid --sort-by. Choose one of: {', '.join(sorted(_EXTERNAL_SORT))}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if sort_order not in {"asc", "desc"}:
        typer.secho("Invalid --sort-order. Use asc or desc.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return
        records = repository.list_external_links(
            run_id=selected_run_id,
            search=search,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_order == "desc",
        )
    finally:
        connection.close()
    items = [
        {
            "target_url": record.target_url,
            "first_seen_at": record.first_seen_at,
            "seen_count": record.seen_count,
        }
        for record in records
    ]
    _print_numbered_items(items, output_format=output_format)


@jobs_app.command("pages-diff")
def pages_diff(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Target run id; defaults to latest."),
    change: str = typer.Option("appeared", "--change", help="appeared|disappeared|all"),
    limit: int = typer.Option(100, "--limit", min=1, help="Max rows per change type."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show page URL diffs between selected run and previous run."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if change not in {"appeared", "disappeared", "all"}:
        typer.secho("Invalid --change. Use appeared, disappeared, or all.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return
        rows: list[dict[str, Any]] = []
        if change in {"appeared", "all"}:
            rows.extend(
                {
                    "change": "appeared",
                    "run_id": rec.run_id,
                    "compared_to_run_id": rec.compared_to_run_id,
                    "url": rec.url,
                    "created_at": rec.created_at,
                }
                for rec in repository.list_page_diffs(run_id=selected_run_id, appeared=True, limit=limit)
            )
        if change in {"disappeared", "all"}:
            rows.extend(
                {
                    "change": "disappeared",
                    "run_id": rec.run_id,
                    "compared_to_run_id": rec.compared_to_run_id,
                    "url": rec.url,
                    "created_at": rec.created_at,
                }
                for rec in repository.list_page_diffs(run_id=selected_run_id, appeared=False, limit=limit)
            )
    finally:
        connection.close()
    _print_numbered_items(rows, output_format=output_format)


@jobs_app.command("external-links-diff")
def external_links_diff(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Target run id; defaults to latest."),
    change: str = typer.Option("appeared", "--change", help="appeared|disappeared|all"),
    limit: int = typer.Option(100, "--limit", min=1, help="Max rows per change type."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show external-link diffs between selected run and previous run."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if change not in {"appeared", "disappeared", "all"}:
        typer.secho("Invalid --change. Use appeared, disappeared, or all.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return
        rows: list[dict[str, Any]] = []
        if change in {"appeared", "all"}:
            rows.extend(
                {
                    "change": "appeared",
                    "run_id": rec.run_id,
                    "compared_to_run_id": rec.compared_to_run_id,
                    "target_url": rec.target_url,
                    "created_at": rec.created_at,
                }
                for rec in repository.list_external_link_diffs(run_id=selected_run_id, appeared=True, limit=limit)
            )
        if change in {"disappeared", "all"}:
            rows.extend(
                {
                    "change": "disappeared",
                    "run_id": rec.run_id,
                    "compared_to_run_id": rec.compared_to_run_id,
                    "target_url": rec.target_url,
                    "created_at": rec.created_at,
                }
                for rec in repository.list_external_link_diffs(run_id=selected_run_id, appeared=False, limit=limit)
            )
    finally:
        connection.close()
    _print_numbered_items(rows, output_format=output_format)


@jobs_app.command("external-link-sources")
def external_link_sources(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Crawl run id; defaults to latest."),
    target_url: str = typer.Option(..., "--target-url", help="External URL exactly as stored."),
    limit: int = typer.Option(500, "--limit", min=1, help="Max source pages to list."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """List internal pages in a run that link to a given external URL."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return
        records = repository.list_source_pages_for_external(
            selected_run_id,
            target_url=target_url,
            limit=limit,
        )
    finally:
        connection.close()
    items = [
        {"source_page_url": r.source_page_url, "first_seen_at": r.first_seen_at}
        for r in records
    ]
    _print_numbered_items(items, output_format=output_format)


@jobs_app.command("pages-content-metrics")
def pages_content_metrics(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Crawl run id; defaults to latest."),
    only_significant: bool = typer.Option(
        False,
        "--only-significant",
        help="Only rows at or above significant_change_threshold_percent vs previous run.",
    ),
    limit: int = typer.Option(200, "--limit", min=1, help="Max rows."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show main-text change metrics vs the previous run (similarity and change_percent)."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return
        records = repository.list_page_content_metrics(
            selected_run_id,
            only_significant=only_significant,
            limit=limit,
        )
    finally:
        connection.close()
    items = [
        {
            "url": r.url,
            "text_similarity_prev": r.text_similarity_prev,
            "text_change_percent_prev": r.text_change_percent_prev,
            "text_compared_to_run_id": r.text_compared_to_run_id,
            "text_significant_change": r.text_significant_change,
        }
        for r in records
    ]
    _print_numbered_items(items, output_format=output_format)


@jobs_app.command("page-text")
def page_text(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Crawl run id; defaults to latest."),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Page URL lookup key: exact match first; falls back to partial URL search.",
    ),
    search: str | None = typer.Option(None, "--search", help="Filter when listing pages for interactive pick."),
    limit: int = typer.Option(100, "--limit", min=1, help="Max pages to list when --url is omitted."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show stored extracted main text for a page URL in a selected run."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        selected_run_id = run_id if run_id is not None else repository.get_latest_run_id(config["meta"]["job_id"])
        if selected_run_id is None:
            typer.echo("No crawl run found for this job.")
            return

        selected_url = url
        if selected_url is None:
            candidates = repository.list_pages_with_text(selected_run_id, search=search, limit=limit)
            if not candidates:
                typer.echo("No pages with stored text found for this run.")
                return
            if output_format == "json":
                payload = [
                    {
                        "index": index,
                        "url": row.url,
                        "text_len": row.text_len,
                        "created_at": row.created_at,
                    }
                    for index, row in enumerate(candidates, start=1)
                ]
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                return
            _print_numbered_items(
                [
                    {"url": row.url, "text_len": row.text_len, "created_at": row.created_at}
                    for row in candidates
                ],
                output_format="table",
            )
            picked = typer.prompt("Select page #", type=int)
            if picked < 1 or picked > len(candidates):
                typer.secho("Selection out of range.", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
            selected_url = candidates[picked - 1].url
            record = repository.get_page_text(selected_run_id, selected_url)
        else:
            record = repository.get_page_text(selected_run_id, selected_url)
            if record is None:
                candidates = repository.list_pages_with_text(selected_run_id, search=selected_url, limit=limit)
                if not candidates:
                    typer.echo("No stored text found for that page URL in this run.")
                    return
                if output_format == "json":
                    payload = [
                        {
                            "index": index,
                            "url": row.url,
                            "text_len": row.text_len,
                            "created_at": row.created_at,
                        }
                        for index, row in enumerate(candidates, start=1)
                    ]
                    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                    return
                _print_numbered_items(
                    [
                        {"url": row.url, "text_len": row.text_len, "created_at": row.created_at}
                        for row in candidates
                    ],
                    output_format="table",
                )
                picked = typer.prompt("Select page #", type=int)
                if picked < 1 or picked > len(candidates):
                    typer.secho("Selection out of range.", fg=typer.colors.RED, err=True)
                    raise typer.Exit(code=1)
                record = repository.get_page_text(selected_run_id, candidates[picked - 1].url)
    finally:
        connection.close()

    if record is None:
        typer.echo("No stored text found for that page URL in this run.")
        return

    if output_format == "json":
        payload = {
            "run_id": record.run_id,
            "url": record.url,
            "created_at": record.created_at,
            "text_len": record.text_len,
            "main_text": record.main_text,
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"run_id={record.run_id}")
    typer.echo(f"url={record.url}")
    typer.echo(f"created_at={record.created_at}")
    typer.echo(f"text_len={record.text_len}")
    typer.echo("")
    typer.echo(record.main_text)


@jobs_app.command("db-stats")
def db_stats(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show DB table row counts and file size."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(config, db)
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        counts = repository.get_table_counts()
        distinct = repository.get_distinct_link_counts()
    finally:
        connection.close()
    byte_size = db_path.stat().st_size if db_path.exists() else 0
    human = format_bytes_human(byte_size)
    thousands = f"{byte_size:,}".replace(",", " ")

    if output_format == "json":
        payload = {
            "table_row_counts": dict(sorted(counts.items())),
            "external_urls_distinct": distinct["external_urls_distinct"],
            "internal_urls_distinct": distinct["internal_urls_distinct"],
            "db_file_size_bytes": byte_size,
            "db_file_size_human": human,
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    rows: list[dict[str, Any]] = [{"metric": name, "value": str(value)} for name, value in sorted(counts.items())]
    rows.append({"metric": "external_urls_distinct", "value": str(distinct["external_urls_distinct"])})
    rows.append({"metric": "internal_urls_distinct", "value": str(distinct["internal_urls_distinct"])})
    rows.append({"metric": "db_file_size", "value": f"{human} ({thousands} B)"})
    _print_db_stats(rows, output_format=output_format)


_PURGE_TASK_TYPES = {"crawl", "link-check"}


def _delete_artifact_files(artifacts_dir: Path, filenames: list[str]) -> tuple[int, int]:
    """Delete unique artifact files; return (deleted, missing). Tolerates missing files."""
    deleted = 0
    missing = 0
    seen: set[str] = set()
    for name in filenames:
        if name in seen:
            continue
        seen.add(name)
        candidate = artifacts_dir / name
        try:
            candidate.unlink()
            deleted += 1
        except FileNotFoundError:
            missing += 1
        except OSError:
            missing += 1
    return deleted, missing


@jobs_app.command("purge")
def purge(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    task_type: str = typer.Option(..., "--task-type", help="crawl|link-check"),
    run_id: int = typer.Option(..., "--run-id", min=1, help="Run id (crawl_runs.id or link_check_runs.id depending on --task-type)."),
    and_older: bool = typer.Option(
        False,
        "--and-older",
        help="Also delete every run of this task-type whose id is <= --run-id (oldest-first cleanup).",
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the interactive confirmation prompt."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    artifacts_dir: Path | None = typer.Option(
        None,
        "--artifacts-dir",
        help="Override the artifacts directory used for on-disk PNG cleanup (defaults to the job's artifacts dir).",
    ),
) -> None:
    """Permanently delete crawl or link-check runs and cascaded data from the job DB.

    Cascades via SQLite foreign keys (PRAGMA foreign_keys=ON):
      crawl    -> crawl_pages, crawl_links, run_pages, run_external_links,
                  run_page_external_links, run_*_appeared/disappeared,
                  link_check_runs, link_check_results, link_check_screenshots
      link-check -> link_check_results, link_check_screenshots

    Job-level link state survives a purge: link_ignore_rules, link_alerts
    (including paused/ignored buckets), link_alert_events, link_failure_state,
    link_retest_queue. Stale link_alerts.last_reported_run_id values are NULLed.

    On-disk PNG artifacts referenced by deleted link_check_screenshots rows
    are also removed from jobs/data/<job_id>/artifacts/.
    """
    if task_type not in _PURGE_TASK_TYPES:
        typer.secho(
            f"Invalid --task-type {task_type!r}. Use one of: {', '.join(sorted(_PURGE_TASK_TYPES))}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    job_id = config["meta"]["job_id"]
    paths = build_job_paths(job_id)
    db_path = _resolve_db_path(config, db)
    artifacts_root = artifacts_dir.resolve() if artifacts_dir is not None else paths.artifacts_dir
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        if task_type == "crawl":
            target_runs = repository.list_crawl_runs_for_purge(
                job_id=job_id, run_id=run_id, and_older=and_older
            )
            if not target_runs:
                typer.secho(
                    f"No crawl run with id {run_id} found for job_id={job_id}.",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(code=1)
            run_ids = [record.run_id for record in target_runs]
            cascade_counts = repository.get_purge_preview_counts_crawl(run_ids)
            artifact_files = repository.list_artifact_files_for_crawl_runs(run_ids)
            alerts_to_null = repository.count_link_alerts_referencing_runs(
                job_id=job_id, run_ids=run_ids
            )
            run_rows = [
                {
                    "run_id": rec.run_id,
                    "started_at": rec.started_at,
                    "finished_at": rec.finished_at or "",
                    "pages_visited": rec.pages_visited,
                    "pages_failed": rec.pages_failed,
                    "links_discovered": rec.links_discovered,
                }
                for rec in target_runs
            ]
        else:
            target_lc_runs = repository.list_link_check_runs_for_purge(
                job_id=job_id, run_id=run_id, and_older=and_older
            )
            if not target_lc_runs:
                typer.secho(
                    f"No link-check run with id {run_id} found for job_id={job_id}.",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(code=1)
            run_ids = [record.run_id for record in target_lc_runs]
            cascade_counts = repository.get_purge_preview_counts_link_check(run_ids)
            artifact_files = repository.list_artifact_files_for_link_check_runs(run_ids)
            alerts_to_null = 0
            run_rows = [
                {
                    "run_id": rec.run_id,
                    "based_on_crawl_run_id": rec.based_on_crawl_run_id,
                    "started_at": rec.started_at,
                    "finished_at": rec.finished_at or "",
                    "checked_total": rec.checked_total,
                    "failed_total": rec.failed_total,
                    "errored_total": rec.errored_total,
                }
                for rec in target_lc_runs
            ]

        unique_artifact_files = sorted(set(artifact_files))
        scope_label = (
            f"crawl run id={run_id}{' and older' if and_older else ''}"
            if task_type == "crawl"
            else f"link-check run id={run_id}{' and older' if and_older else ''}"
        )
        typer.secho(
            f"Purge plan for job_id={job_id} ({scope_label}):",
            fg=typer.colors.CYAN,
        )
        typer.echo(f"db={db_path}")
        _print_numbered_items(run_rows, output_format="table")
        cascade_rows = [
            {"table": name, "rows_to_delete": str(value)}
            for name, value in sorted(cascade_counts.items())
        ]
        cascade_rows.append({"table": "artifact_files_to_delete", "rows_to_delete": str(len(unique_artifact_files))})
        if task_type == "crawl":
            cascade_rows.append(
                {"table": "link_alerts.last_reported_run_id_nulled", "rows_to_delete": str(alerts_to_null)}
            )
        _render_table(["table", "rows_to_delete"], [(row["table"], row["rows_to_delete"]) for row in cascade_rows])
        typer.secho(
            "Job-level link state preserved: link_ignore_rules, link_alerts, "
            "link_alert_events, link_failure_state, link_retest_queue.",
            fg=typer.colors.CYAN,
        )

        if not yes:
            confirmed = typer.confirm("Proceed with purge?", default=False)
            if not confirmed:
                typer.secho("Aborted. No data was deleted.", fg=typer.colors.YELLOW)
                raise typer.Exit(code=1)

        if task_type == "crawl":
            nulled = repository.null_link_alert_last_run_for_runs(
                job_id=job_id, run_ids=run_ids
            )
            repository.delete_crawl_runs(run_ids)
        else:
            nulled = 0
            repository.delete_link_check_runs(run_ids)
    finally:
        connection.close()

    deleted_files, missing_files = _delete_artifact_files(artifacts_root, unique_artifact_files)

    typer.secho(
        (
            f"Purge complete: task_type={task_type} "
            f"deleted_runs={len(run_ids)} "
            f"deleted_run_ids={run_ids} "
            f"artifact_files_deleted={deleted_files} "
            f"artifact_files_missing={missing_files} "
            f"link_alerts_nulled={nulled}"
        ),
        fg=typer.colors.GREEN,
    )
