"""`blink check` command group."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config.loader import load_effective_job_config
from app.config.schema import validate_job_config
from app.link_check.composite_checkers import HttpThenPlaywrightChecker, PreflightAssetSkippingChecker
from app.link_check.http_client import HttpCheckResult, HttpLinkChecker
from app.link_check.playwright_checker import PlaywrightLinkChecker, PlaywrightLinkCheckerConfig
from app.link_check.reporting import build_link_check_report, report_filename, write_link_check_report
from app.link_check.runner import run_link_check
from app.models.job_config import JobConfig
from app.notifications.models import BrokenLinkNotification, SourceBlinkRef
from app.notifications.service import NotificationService
from app.notifications.slack.retest_worker import process_pending_link_retests
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.db_errors import exit_on_sqlite_failure
from app.runtime.job_paths import build_job_paths
from app.runtime.job_prepare import prepare_job_database, prepare_job_runtime
from app.runtime.logging import configure_logging, event_logger
from app.runtime.status import LiveStatus
from app.render.playwright_client import BrowserSettings, BrowserViewport

link_check_app = typer.Typer(help="Run Blink link checks.")
ignore_app = typer.Typer(help="Manage manual ignore rules for external link failures.")
_console = Console()


def _emit_runtime_notes(notes: list[str]) -> None:
    for line in notes:
        typer.secho(line, fg=typer.colors.CYAN)
        event_logger("runtime.job_layout").info(line)


def _browser_settings_from_config(config: JobConfig) -> BrowserSettings:
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


def _build_link_checker(config: JobConfig, *, artifacts_dir: Path):
    implementation = str(config["link_check"]["implementation"]).strip().lower()
    lc = config["link_check"]
    pw_cfg = lc["playwright"]

    def _make_playwright_core() -> PlaywrightLinkChecker:
        return PlaywrightLinkChecker(
            browser_settings=_browser_settings_from_config(config),
            config=PlaywrightLinkCheckerConfig(
                navigation_timeout_seconds=pw_cfg["navigation_timeout_seconds"],
                network_idle_seconds=pw_cfg["network_idle_seconds"],
                settle_wait_seconds=pw_cfg["settle_wait_seconds"],
                wait_until=pw_cfg["wait_until"],
                accept_partial_success_on_navigation_timeout=pw_cfg["accept_partial_success_on_navigation_timeout"],
                artifacts_dir=artifacts_dir,
                save_failure_screenshot=lc["save_failure_screenshot"],
                restart_browser_every_n_checks=int(pw_cfg.get("restart_browser_every_n_checks") or 0),
            ),
        )

    if implementation == "http":
        return HttpLinkChecker(
            timeout_seconds=lc["request_timeout_seconds"],
            follow_redirects=lc["follow_redirects"],
            artifacts_dir=artifacts_dir,
            save_failure_screenshot=lc["save_failure_screenshot"],
            user_agent=config["crawl"]["user_agent"],
        )
    if implementation == "playwright":
        if not lc["follow_redirects"]:
            event_logger("linkcheck.redirects").warning(
                "link_check.follow_redirects=false is ignored for Playwright implementation."
            )
        return PreflightAssetSkippingChecker(config=config, delegate=_make_playwright_core())
    if implementation == "http_then_playwright":
        http_checker = HttpLinkChecker(
            timeout_seconds=lc["request_timeout_seconds"],
            follow_redirects=lc["follow_redirects"],
            artifacts_dir=artifacts_dir,
            save_failure_screenshot=lc["save_failure_screenshot"],
            user_agent=config["crawl"]["user_agent"],
        )
        return HttpThenPlaywrightChecker(
            config=config,
            http_checker=http_checker,
            playwright_checker=_make_playwright_core(),
        )
    raise ValueError(f"Unsupported link_check.implementation: {implementation}")


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


def _emit_live_failure(
    url: str,
    result: HttpCheckResult,
    *,
    console: Console,
    source_refs: list[dict[str, str | None]] | None = None,
    run_id: int | str = "-",
) -> None:
    if result.ok:
        return
    reason = result.error_message or (f"HTTP {result.status_code}" if result.status_code is not None else "Unknown error")
    console.print(f"check failed: {url} -> {reason}", style="yellow")
    for ref in source_refs or []:
        page_url = ref.get("page_url", "")
        anchor_text = ref.get("anchor_text")
        console.print(f"└ on {page_url}", style="yellow")
        if anchor_text:
            console.print(f"  ↳ text: {anchor_text}", style="yellow")
    # Keep in file logs but avoid noisy duplicate console warnings.
    event_logger("linkcheck.failure", run_id=run_id).debug(f"url={url} reason={reason}")


def _send_reportable_notifications(
    config: JobConfig,
    *,
    run_id: int,
    db_path: Path,
    max_blinks_override: int | None = None,
) -> None:
    notifications = config["notifications"]
    if not notifications["enabled"]:
        return
    if not notifications["destinations"]:
        return

    report_connection = connect_sqlite(db_path)
    initialize_schema(report_connection)
    report_repo = CrawlRepository(report_connection)
    now = datetime.now(tz=UTC)
    try:
        latest_results = report_repo.list_latest_link_check_results(run_id)
        reportables = [row for row in latest_results if row.decision_state == "reportable"]
        reportable_targets = {row.target_url for row in reportables}
        report_repo.resolve_link_alerts_not_in_targets(
            job_id=config["meta"]["job_id"],
            active_targets=reportable_targets,
            resolved_at=now.isoformat(),
        )
        report_repo.expire_link_alert_human_buckets(job_id=config["meta"]["job_id"], now_iso=now.isoformat())
        if not reportables:
            return
        source_refs = report_repo.list_source_page_refs_for_targets(
            run_id,
            [result.target_url for result in reportables],
        )
        open_alerts = {
            row.target_url: row
            for row in report_repo.list_open_link_alerts(job_id=config["meta"]["job_id"])
        }
    finally:
        report_connection.close()

    service = NotificationService()
    max_blinks = max_blinks_override if max_blinks_override is not None else config["notifications"]["max_blinks_per_run"]
    max_blinks = max(1, int(max_blinks))
    new_reportables = [row for row in reportables if row.target_url not in open_alerts]
    existing_reportables = [row for row in reportables if row.target_url in open_alerts]

    def _parse_alert_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    selected_new = new_reportables[:max_blinks]
    selected_existing = []
    for row in existing_reportables:
        alert = open_alerts[row.target_url]
        reminder_days: list[int] = []
        for destination in config["notifications"]["destinations"]:
            if destination["enabled"] and destination["reminders"]["enabled"]:
                reminder_days.extend(destination["reminders"]["days_after_first_alert"])
        reminder_days = sorted({int(day) for day in reminder_days if int(day) > 0})
        if not reminder_days:
            continue
        reminder_index = alert.reminder_sent_count
        if reminder_index >= len(reminder_days):
            continue
        if alert.human_bucket == "ignored":
            if alert.ignore_until is None:
                continue
            if now < _parse_alert_timestamp(alert.ignore_until):
                continue
        if alert.hold_until and now < _parse_alert_timestamp(alert.hold_until):
            continue
        age_days = (now - _parse_alert_timestamp(alert.first_reported_at)).total_seconds() / 86400.0
        hold_due = False
        if alert.hold_until:
            hold_due = now >= _parse_alert_timestamp(alert.hold_until)
        if hold_due or age_days >= float(reminder_days[reminder_index]):
            selected_existing.append(row)

    selected_reportables = [*selected_new, *selected_existing]
    report_connection = connect_sqlite(db_path)
    initialize_schema(report_connection)
    report_repo = CrawlRepository(report_connection)
    try:
        for result in selected_reportables:
            existing_alert = open_alerts.get(result.target_url)
            payload = BrokenLinkNotification(
                job_id=config["meta"]["job_id"],
                run_id=run_id,
                target_url=result.target_url,
                checked_at=result.checked_at,
                status_code=result.status_code,
                error_message=result.error_message,
                decision_state=result.decision_state or "reportable",
                source_refs=[
                    SourceBlinkRef(page_url=ref.source_page_url, anchor_text=ref.anchor_text)
                    for ref in source_refs.get(result.target_url, [])
                ],
                first_detected_at=(
                    existing_alert.first_reported_at
                    if existing_alert is not None
                    else result.checked_at
                ),
            )
            dispatches = service.send_broken_link(notifications, payload)
            sent_ok = any(dispatch.success for dispatch in dispatches)
            if sent_ok:
                report_repo.upsert_open_link_alert(
                    job_id=config["meta"]["job_id"],
                    target_url=result.target_url,
                    run_id=run_id,
                    checked_at=result.checked_at,
                    status_code=result.status_code,
                    error_message=result.error_message,
                )
                refreshed = report_repo.get_open_link_alert_by_target(
                    job_id=config["meta"]["job_id"],
                    target_url=result.target_url,
                )
                if refreshed is not None:
                    for dispatch in dispatches:
                        if dispatch.success and dispatch.slack_root_ts and dispatch.slack_channel_id:
                            report_repo.update_link_alert_slack_refs(
                                alert_id=refreshed.alert_id,
                                slack_destination_id=dispatch.destination_id,
                                slack_channel_id=dispatch.slack_channel_id,
                                slack_root_ts=dispatch.slack_root_ts,
                                slack_thread_ts=dispatch.slack_root_ts,
                                slack_bootstrap_ts=dispatch.slack_bootstrap_ts,
                            )
                if result.target_url in open_alerts:
                    report_repo.increment_link_alert_reminder_count(
                        job_id=config["meta"]["job_id"],
                        target_url=result.target_url,
                    )
            for dispatch in dispatches:
                if dispatch.success:
                    event_logger("notifications.dispatch", run_id=run_id).info(
                        f"provider={dispatch.provider} destination={dispatch.destination_id} target_url={result.target_url}"
                    )
                else:
                    event_logger("notifications.dispatch_failed", run_id=run_id).warning(
                        f"provider={dispatch.provider} destination={dispatch.destination_id} "
                        f"target_url={result.target_url} error={dispatch.error or 'unknown'}"
                    )
    finally:
        report_connection.close()


link_check_app.add_typer(ignore_app, name="ignore")


@ignore_app.command("add")
def ignore_add(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    target_url: str | None = typer.Option(None, "--target-url", help="Exact URL to ignore."),
    pattern: str | None = typer.Option(None, "--pattern", help="Pattern to match ignored URLs."),
    match_type: str = typer.Option("contains", "--match-type", help="contains|exact (used with --pattern)."),
    days: int | None = typer.Option(None, "--days", min=1, help="Optional expiry in days."),
    reason: str | None = typer.Option(None, "--reason", help="Human-friendly reason."),
) -> None:
    """Add a manual ignore rule (exact URL or partial contains pattern)."""
    if target_url is None and pattern is None:
        typer.secho("Provide either --target-url or --pattern.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if target_url is not None and pattern is not None:
        typer.secho("Provide only one of --target-url or --pattern.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if match_type not in {"contains", "exact"}:
        typer.secho("Invalid --match-type. Use contains or exact.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    paths = build_job_paths(config["meta"]["job_id"])
    db_path = db.resolve() if db else paths.db_path
    expires_at = (datetime.now(tz=UTC) + timedelta(days=days)).isoformat() if days is not None else None
    final_match_type = "exact" if target_url is not None else match_type
    final_pattern = target_url if target_url is not None else (pattern or "")

    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        rule_id = repository.add_link_ignore_rule(
            job_id=config["meta"]["job_id"],
            match_type=final_match_type,
            pattern=final_pattern,
            reason=reason,
            expires_at=expires_at,
            created_by="cli",
            source="cli",
        )
    finally:
        connection.close()
    typer.secho(f"Ignore rule added: id={rule_id}", fg=typer.colors.GREEN)


@ignore_app.command("list")
def ignore_list(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    active_only: bool = typer.Option(False, "--active-only", help="Show only active rules."),
    search: str | None = typer.Option(None, "--search", help="Substring filter on pattern."),
    limit: int = typer.Option(200, "--limit", min=1, help="Max rows."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """List ignore rules for the selected job."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    paths = build_job_paths(config["meta"]["job_id"])
    db_path = db.resolve() if db else paths.db_path
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        rows = repository.list_link_ignore_rules(
            job_id=config["meta"]["job_id"],
            active_only=active_only,
            search=search,
            limit=limit,
        )
    finally:
        connection.close()
    payload = [
        {
            "id": row.rule_id,
            "match_type": row.match_type,
            "pattern": row.pattern,
            "reason": row.reason or "",
            "active": row.active,
            "expires_at": row.expires_at or "",
            "created_at": row.created_at,
            "source": row.source,
        }
        for row in rows
    ]
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    _render_rows_table(payload)


@ignore_app.command("remove")
def ignore_remove(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    rule_id: int = typer.Option(..., "--id", min=1, help="Ignore rule id to deactivate."),
) -> None:
    """Deactivate one ignore rule."""
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    paths = build_job_paths(config["meta"]["job_id"])
    db_path = db.resolve() if db else paths.db_path
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        removed = repository.deactivate_link_ignore_rule(job_id=config["meta"]["job_id"], rule_id=rule_id)
    finally:
        connection.close()
    if not removed:
        typer.secho("No active ignore rule found for that id.", fg=typer.colors.YELLOW)
        return
    typer.secho(f"Ignore rule deactivated: id={rule_id}", fg=typer.colors.GREEN)


@ignore_app.command("impact")
def ignore_impact(
    job: str = typer.Option(..., "--job", help="Path to job JSON file."),
    db: Path | None = typer.Option(None, "--db", help="SQLite DB path override."),
    run_id: int | None = typer.Option(None, "--run-id", help="Crawl run id; defaults to latest run."),
    rule_id: int | None = typer.Option(None, "--rule-id", min=1, help="Filter by one ignore rule id."),
    active_only: bool = typer.Option(True, "--active-only/--include-inactive", help="Only active rules."),
    details: bool = typer.Option(False, "--details", help="Include detailed affected links/pages per rule."),
    limit: int = typer.Option(2000, "--limit", min=1, help="Max matched impact rows."),
    output_format: str = typer.Option("table", "--format", help="Output format: table|json."),
) -> None:
    """Show which current run links/pages are affected by ignore rules."""
    if output_format not in {"table", "json"}:
        typer.secho("Invalid format. Use table or json.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        config = load_effective_job_config(job)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"Failed to load job config: {exc}", fg=typer.colors.RED, err=True)
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
        rows = repository.list_ignore_rule_impacts(
            job_id=config["meta"]["job_id"],
            run_id=selected_run_id,
            active_only=active_only,
            rule_id=rule_id,
            limit=limit,
        )
    finally:
        connection.close()

    grouped: dict[int, dict[str, object]] = {}
    for row in rows:
        entry = grouped.get(row.rule_id)
        if entry is None:
            entry = {
                "rule_id": row.rule_id,
                "match_type": row.match_type,
                "pattern": row.pattern,
                "reason": row.reason or "",
                "active": row.active,
                "expires_at": row.expires_at or "",
                "affected_targets": set(),
                "affected_blinks": 0,
                "examples": [],
                "details": [],
            }
            grouped[row.rule_id] = entry
        targets = entry["affected_targets"]
        assert isinstance(targets, set)
        targets.add(row.target_url)
        if row.source_page_url:
            entry["affected_blinks"] = int(entry["affected_blinks"]) + 1
        examples = entry["examples"]
        assert isinstance(examples, list)
        if row.target_url not in examples and len(examples) < 3:
            examples.append(row.target_url)
        detail_rows = entry["details"]
        assert isinstance(detail_rows, list)
        detail_rows.append(
            {
                "target_url": row.target_url,
                "source_page_url": row.source_page_url or "",
                "anchor_text": row.anchor_text or "",
            }
        )

    summary_rows: list[dict[str, object]] = []
    for rule_id_key in sorted(grouped):
        entry = grouped[rule_id_key]
        targets = entry["affected_targets"]
        assert isinstance(targets, set)
        summary_rows.append(
            {
                "run_id": selected_run_id,
                "rule_id": entry["rule_id"],
                "match_type": entry["match_type"],
                "pattern": entry["pattern"],
                "active": entry["active"],
                "expires_at": entry["expires_at"],
                "affected_external_urls": len(targets),
                "affected_blinks": entry["affected_blinks"],
                "examples": ", ".join(entry["examples"]),
                "reason": entry["reason"],
            }
        )

    if output_format == "json":
        payload = {
            "run_id": selected_run_id,
            "rows": summary_rows,
            "details": (
                {
                    str(rule_id_key): grouped[rule_id_key]["details"]
                    for rule_id_key in sorted(grouped)
                }
                if details
                else {}
            ),
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if not summary_rows:
        typer.echo("No impacted links/pages found for selected rules and run.")
        return
    _render_rows_table(summary_rows)
    if details:
        for rule_id_key in sorted(grouped):
            detail_rows = grouped[rule_id_key]["details"]
            assert isinstance(detail_rows, list)
            if not detail_rows:
                continue
            typer.echo("")
            typer.echo(f"rule_id={rule_id_key} details")
            _render_rows_table(detail_rows)


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
        screenshots_by_result_id = repository.list_latest_screenshots_by_result_ids([row.row_id for row in records])
        source_refs_by_target = repository.list_source_page_refs_for_targets(
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
            "error_category": record.error_category or "",
            "decision_state": record.decision_state or "",
            "decision_reason": record.decision_reason or "",
            "ignore_rule_id": record.ignore_rule_id if record.ignore_rule_id is not None else "",
            "screenshot_id": (
                screenshots_by_result_id[record.row_id].artifact_file
                if record.row_id in screenshots_by_result_id
                else ""
            ),
            "checked_at": record.checked_at,
            "source_pages": "; ".join(
                [ref.source_page_url for ref in source_refs_by_target.get(record.target_url, [])]
            ),
            "source_anchor_texts": " | ".join(
                [ref.anchor_text or "" for ref in source_refs_by_target.get(record.target_url, []) if ref.anchor_text]
            ),
            "check_meta": record.check_meta or "",
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
    run_id: int | None = typer.Option(
        None,
        "--run-id",
        help="Optional crawl run id to check; defaults to the latest completed crawl run.",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Optional max number of links to check."),
    show_live_failures: bool = typer.Option(
        True,
        "--show-live-failures/--hide-live-failures",
        help="Print each failed URL and reason during checking.",
    ),
    show_progress: bool = typer.Option(
        True,
        "--show-progress/--hide-progress",
        help="Update live status line with the current URL being checked.",
    ),
    max_blinks: int | None = typer.Option(
        None,
        "--max-blinks",
        min=1,
        help="Max new broken-link notifications to send in this run.",
    ),
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
        checker = _build_link_checker(config, artifacts_dir=paths.artifacts_dir)
        open_session = getattr(checker, "open_session", None)
        if callable(open_session):
            open_session()
        selected_run_id = (
            run_id if run_id is not None else repository.get_latest_completed_run_id(config["meta"]["job_id"])
        )
        link_check_run_id: int | None = None
        source_refs_by_target: dict[str, list[dict[str, str | None]]] = {}
        preexisting_reportable_targets: set[str] = set()
        if selected_run_id is not None and show_live_failures:
            links_for_check = repository.list_links_for_check(crawl_run_id=selected_run_id, limit=limit)
            refs = repository.list_source_page_refs_for_targets(
                selected_run_id,
                [link.target_url for link in links_for_check],
            )
            source_refs_by_target = {
                target: [
                    {"page_url": ref.source_page_url, "anchor_text": ref.anchor_text}
                    for ref in ref_list
                ]
                for target, ref_list in refs.items()
            }
        preexisting_reportable_targets = {
            row.target_url for row in repository.list_open_link_alerts(job_id=config["meta"]["job_id"])
        }
        if selected_run_id is not None:
            link_check_run_id = repository.create_link_check_run(
                job_id=config["meta"]["job_id"],
                based_on_crawl_run_id=selected_run_id,
                started_at=datetime.now(tz=UTC).isoformat(),
            )
        link_check_started_at = datetime.now(tz=UTC).isoformat()
        summary = None
        try:
            process_pending_link_retests(config=config, repository=repository, checker=checker, limit=10)
            summary = run_link_check(
                config=config,
                repository=repository,
                checker=checker,
                run_id=selected_run_id,
                link_check_run_id=link_check_run_id,
                limit=limit,
                max_reportable_failures=max_blinks,
                preexisting_reportable_targets=preexisting_reportable_targets,
                status_hook=status.update if show_progress else None,
                result_hook=(
                    (
                        lambda url, result: _emit_live_failure(
                            url,
                            result,
                            console=status.console,
                            source_refs=source_refs_by_target.get(url, []),
                            run_id=selected_run_id or "-",
                        )
                    )
                    if show_live_failures
                    else None
                ),
            )
            link_check_finished_at = datetime.now(tz=UTC).isoformat()
            if link_check_run_id is not None:
                repository.finish_link_check_run(
                    link_check_run_id=link_check_run_id,
                    finished_at=link_check_finished_at,
                    checked_total=summary.checked,
                    passed_total=summary.passed,
                    failed_total=summary.failed,
                    errored_total=summary.errored,
                    ignored_total=summary.ignored,
                    pending_tolerance_total=summary.pending_tolerance,
                    reportable_failures_total=summary.reportable_failures,
                )
        except sqlite3.Error as exc:
            exit_on_sqlite_failure(
                exc,
                context="Link-check",
                job_id=config["meta"]["job_id"],
                db_path=str(db_path),
            )
        finally:
            close_session = getattr(checker, "close_session", None)
            if callable(close_session):
                close_session()
            connection.close()
            status.update("Link-check finished")

    if summary is None or summary.crawl_run_id is None:
        typer.secho(
            "No completed crawl run found for this job. If a crawl is still running, wait for it to finish; "
            "otherwise run `blink crawl run` first.",
            fg=typer.colors.YELLOW,
        )
        event_logger("linkcheck.no_crawl_run").warning("No completed crawl run found for this job.")
        return

    typer.secho(
        (
            f"Link-check complete: link_check_run_id={summary.link_check_run_id}, "
            f"based_on_crawl_run_id={summary.crawl_run_id}, "
            f"checked={summary.checked}, passed={summary.passed}, "
            f"failed={summary.failed}, errored={summary.errored}, "
            f"ignored={summary.ignored}, pending_tolerance={summary.pending_tolerance}, "
            f"reportable_failures={summary.reportable_failures}, skipped={summary.skipped}"
        ),
        fg=typer.colors.GREEN,
    )
    event_logger("linkcheck.summary", run_id=summary.crawl_run_id).info(
        (
            f"checked={summary.checked} passed={summary.passed} "
            f"failed={summary.failed} errored={summary.errored} "
            f"ignored={summary.ignored} pending_tolerance={summary.pending_tolerance} "
            f"reportable_failures={summary.reportable_failures} skipped={summary.skipped}"
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
            screenshots_by_result_id = report_repo.list_latest_screenshots_by_result_ids(
                [result.row_id for result in latest_results]
            )
            source_refs = report_repo.list_source_page_refs_for_targets(
                summary.crawl_run_id,
                [result.target_url for result in latest_results],
            )
            source_refs_by_target = {
                target: [
                    {"page_url": ref.source_page_url, "anchor_text": ref.anchor_text}
                    for ref in ref_list
                ]
                for target, ref_list in source_refs.items()
            }
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
            ignored=summary.ignored,
            pending_tolerance=summary.pending_tolerance,
            reportable_failures=summary.reportable_failures,
            results=latest_results,
            source_refs_by_target=source_refs_by_target,
            screenshot_by_result_id={result_id: row.artifact_file for result_id, row in screenshots_by_result_id.items()},
        )
        write_link_check_report(report_path, payload)
        typer.secho(f"Link-check JSON report written: {report_path}", fg=typer.colors.CYAN)
        event_logger("linkcheck.report_written", run_id=summary.crawl_run_id).info(f"path={report_path}")

    _send_reportable_notifications(
        config,
        run_id=summary.crawl_run_id,
        db_path=db_path,
        max_blinks_override=max_blinks,
    )
