"""`blink notifications` command group."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from app.config.loader import load_effective_job_config
from app.config.schema import validate_job_config
from app.notifications.models import NotificationMessage
from app.notifications.service import NotificationService
from app.notifications.slack.http_handler import apply_inbound_slack_from_envelope
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.job_paths import build_job_paths

notifications_app = typer.Typer(help="Notification adapter operations.")
slack_app = typer.Typer(help="Slack lifecycle / Events API helpers.")
notifications_app.add_typer(slack_app, name="slack")


@notifications_app.command("test")
def test_notification(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
) -> None:
    """Send a test notification using job metadata."""
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

    notifications = config["notifications"]
    if not notifications["enabled"] or not any(d["enabled"] for d in notifications["destinations"]):
        typer.secho("No enabled notification destinations for this job.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)

    paths = build_job_paths(config["meta"]["job_id"])
    db_path = db.resolve() if db else paths.db_path
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        latest_run_id = repository.get_latest_run_id(config["meta"]["job_id"])
        history = repository.list_run_history(config["meta"]["job_id"], limit=1)
    finally:
        connection.close()

    if history:
        latest = history[0]
        stats = (
            f"latest_run_id={latest.run_id}, pages_visited={latest.pages_visited}, "
            f"pages_failed={latest.pages_failed}, links_discovered={latest.links_discovered}"
        )
    else:
        stats = "no crawl runs yet"

    body = (
        f"Hello from Blink.\n"
        f"job_id={config['meta']['job_id']}\n"
        f"name={config['meta']['name']}\n"
        f"base_url={config['target']['base_url']}\n"
        f"latest_run={latest_run_id if latest_run_id is not None else '-'}\n"
        f"stats={stats}"
    )
    message = NotificationMessage(
        job_id=config["meta"]["job_id"],
        title=f"Blink notification test ({config['meta']['job_id']})",
        body=body,
    )
    service = NotificationService()
    results = service.send_message(notifications, message)
    if not results:
        typer.secho("No destination accepted test notification.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    success = [row for row in results if row.success]
    failed = [row for row in results if not row.success]
    for row in success:
        typer.secho(f"Test notification sent via {row.provider}:{row.destination_id}", fg=typer.colors.GREEN)
    for row in failed:
        typer.secho(
            f"Test notification failed via {row.provider}:{row.destination_id} -> {row.error or 'unknown'}",
            fg=typer.colors.YELLOW,
        )


@slack_app.command("handle-event")
def slack_handle_event(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    event: Path = typer.Option(..., "--event", exists=True, help="JSON file with a Slack event or Events API envelope."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
) -> None:
    """Apply one normalized Slack lifecycle event (reaction or thread message) to the job database."""
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

    raw = json.loads(event.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        typer.secho("Event JSON must be an object.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    paths = build_job_paths(config["meta"]["job_id"])
    db_path = db.resolve() if db else paths.db_path
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        ok, err = apply_inbound_slack_from_envelope(config, repository, raw)
        if not ok:
            if err == "unparsed":
                typer.secho(
                    "Event did not match any enabled Slack destination or was unparsed/invalid.",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(code=1)
            typer.secho(f"Lifecycle apply failed: {err}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        typer.secho("Lifecycle event applied.", fg=typer.colors.GREEN)
    finally:
        connection.close()
