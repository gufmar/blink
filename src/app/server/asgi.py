"""Starlette ASGI application for Slack Events API."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from app.config.loader import load_effective_job_config, project_root
from app.config.schema import validate_job_config
from app.models.job_config import JobConfig
from app.notifications.slack.http_handler import (
    apply_inbound_slack_from_envelope,
    extract_slack_channel_id,
    notifications_signing_secret_env_name,
    resolve_notifications_signing_secret,
)
from app.notifications.slack.signature import verify_slack_signing_secret
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.job_paths import build_job_paths
from app.schedule.service import BlinkSchedulerService
from app.server.dashboard_page import (
    render_results_job_html,
    render_results_jobs_html,
    render_results_run_html,
    render_schedule_dashboard_html,
)

_SLUG_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _json_error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code}, status_code=status)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def api_schedule(request: Request) -> JSONResponse:
    svc: BlinkSchedulerService = request.app.state.scheduler_service
    return JSONResponse(svc.build_schedule_payload())


def _path_for(request: Request, route_name: str, **path_params: object) -> str:
    return str(request.url_for(route_name, **path_params))


def _load_job_entries(jobs_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for job_path in sorted(jobs_root.glob("*.job.json")):
        if job_path.name.startswith("_"):
            continue
        config, _ = _load_and_validate_job_config(job_path)
        if config is None:
            continue
        entries.append(
            {
                "job_id": config["meta"]["job_id"],
                "name": config["meta"]["name"],
                "enabled": bool(config["meta"]["enabled"]),
                "job_file": str(job_path),
            }
        )
    return entries


def _db_path_for_job(jobs_root: Path, job_id: str) -> Path:
    return jobs_root / job_id / "db" / f"{job_id}.sqlite3"


def _open_repo_if_exists(db_path: Path) -> tuple[CrawlRepository, sqlite3.Connection] | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return CrawlRepository(conn), conn


def _close_repo(repo_and_conn: tuple[CrawlRepository, sqlite3.Connection] | None) -> None:
    if repo_and_conn is None:
        return
    _, conn = repo_and_conn
    conn.close()


def _job_entry_by_id(jobs_root: Path, job_id: str) -> dict[str, Any] | None:
    for entry in _load_job_entries(jobs_root):
        if entry["job_id"] == job_id:
            return entry
    return None


def _serialize_run_history(job_id: str, repo: CrawlRepository | None, *, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        runs = repo.list_run_history(job_id, limit=limit)
    except sqlite3.Error:
        return []
    return [
        {
            "run_id": rec.run_id,
            "started_at": rec.started_at,
            "finished_at": rec.finished_at,
            "pages_visited": rec.pages_visited,
            "pages_failed": rec.pages_failed,
            "links_discovered": rec.links_discovered,
        }
        for rec in runs
    ]


async def schedule_dashboard(request: Request) -> HTMLResponse:
    svc: BlinkSchedulerService = request.app.state.scheduler_service
    payload = svc.build_schedule_payload()
    for task in payload.get("tasks") or []:
        job_id = str(task.get("job_id") or "")
        if job_id:
            task["results_url"] = _path_for(request, "dashboard_results_job", job_id=job_id)
    page = render_schedule_dashboard_html(
        payload,
        links={
            "health": _path_for(request, "health"),
            "schedule_json": _path_for(request, "api_schedule"),
            "slack_health": _path_for(request, "slack_health"),
            "results_index": _path_for(request, "dashboard_results_jobs"),
            "schedule_refresh": _path_for(request, "dashboard_schedule"),
        },
    )
    return HTMLResponse(page)


async def api_results_jobs(request: Request) -> JSONResponse:
    jobs_root: Path = request.app.state.jobs_root
    jobs = _load_job_entries(jobs_root)
    out: list[dict[str, Any]] = []
    for job in jobs:
        repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, str(job["job_id"])))
        repo = repo_and_conn[0] if repo_and_conn else None
        try:
            history = _serialize_run_history(str(job["job_id"]), repo, limit=1)
            out.append(
                {
                    **job,
                    "latest_run": history[0] if history else None,
                }
            )
        finally:
            _close_repo(repo_and_conn)
    return JSONResponse({"jobs": out})


async def api_results_job_runs(request: Request) -> JSONResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return _json_error(404, "job_not_found")
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    repo = repo_and_conn[0] if repo_and_conn else None
    try:
        runs = _serialize_run_history(job_id, repo, limit=100)
    finally:
        _close_repo(repo_and_conn)
    return JSONResponse({"job": job, "runs": runs})


async def api_results_run_detail(request: Request) -> JSONResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    run_id = int(request.path_params["run_id"])
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return _json_error(404, "job_not_found")
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    if repo_and_conn is None:
        return _json_error(404, "run_not_found")
    repo, _ = repo_and_conn
    try:
        run = repo.get_run_record(job_id=job_id, run_id=run_id)
        if run is None:
            return _json_error(404, "run_not_found")
        failed_links = repo.list_latest_failed_link_check_results(run_id, limit=200)
        sources = repo.list_source_page_refs_for_targets(run_id, [row.target_url for row in failed_links])
        failed_pages = repo.list_crawled_pages(run_id, only_failed=True, limit=200)
        return JSONResponse(
            {
                "job": job,
                "run": {
                    "run_id": run.run_id,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "pages_visited": run.pages_visited,
                    "pages_failed": run.pages_failed,
                    "links_discovered": run.links_discovered,
                },
                "failed_links": [
                    {
                        "target_url": row.target_url,
                        "status_code": row.status_code,
                        "error_category": row.error_category,
                        "error_message": row.error_message,
                        "checked_at": row.checked_at,
                        "source_pages": [ref.source_page_url for ref in sources.get(row.target_url, [])],
                    }
                    for row in failed_links
                ],
                "failed_pages": [
                    {
                        "url": row.url,
                        "depth": row.depth,
                        "status_code": row.status_code,
                        "error_message": row.error_message,
                        "created_at": row.created_at,
                    }
                    for row in failed_pages
                ],
            }
        )
    finally:
        _close_repo(repo_and_conn)


async def dashboard_results_jobs(request: Request) -> HTMLResponse:
    payload = (await api_results_jobs(request)).body
    data = json.loads(payload.decode("utf-8"))
    jobs = list(data.get("jobs") or [])
    for job in jobs:
        job["details_url"] = _path_for(request, "dashboard_results_job", job_id=str(job.get("job_id")))
    page = render_results_jobs_html(
        {"jobs": jobs},
        links={
            "schedule": _path_for(request, "dashboard_schedule"),
            "jobs_json": _path_for(request, "api_results_jobs"),
            "health": _path_for(request, "health"),
            "refresh": _path_for(request, "dashboard_results_jobs"),
        },
    )
    return HTMLResponse(page)


async def dashboard_results_job(request: Request) -> HTMLResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return HTMLResponse("Job not found", status_code=404)
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    repo = repo_and_conn[0] if repo_and_conn else None
    try:
        run_rows = _serialize_run_history(job_id, repo, limit=100)
    finally:
        _close_repo(repo_and_conn)
    for run in run_rows:
        run["details_url"] = _path_for(
            request,
            "dashboard_results_run",
            job_id=job_id,
            run_id=int(run["run_id"]),
        )
    page = render_results_job_html(
        job=job,
        run_rows=run_rows,
        links={
            "jobs_index": _path_for(request, "dashboard_results_jobs"),
            "runs_json": _path_for(request, "api_results_job_runs", job_id=job_id),
            "schedule": _path_for(request, "dashboard_schedule"),
            "refresh": _path_for(request, "dashboard_results_job", job_id=job_id),
        },
    )
    return HTMLResponse(page)


async def dashboard_results_run(request: Request) -> HTMLResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    run_id = int(request.path_params["run_id"])
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return HTMLResponse("Job not found", status_code=404)
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    if repo_and_conn is None:
        return HTMLResponse("Run not found", status_code=404)
    repo, _ = repo_and_conn
    try:
        run = repo.get_run_record(job_id=job_id, run_id=run_id)
        if run is None:
            return HTMLResponse("Run not found", status_code=404)
        failed_links = repo.list_latest_failed_link_check_results(run_id, limit=200)
        failed_pages = repo.list_crawled_pages(run_id, only_failed=True, limit=200)
        sources = repo.list_source_page_refs_for_targets(run_id, [row.target_url for row in failed_links])
    finally:
        _close_repo(repo_and_conn)
    run_data = {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "pages_visited": run.pages_visited,
        "pages_failed": run.pages_failed,
        "links_discovered": run.links_discovered,
    }
    page = render_results_run_html(
        job=job,
        run=run_data,
        failed_links=[
            {
                "target_url": row.target_url,
                "status_code": row.status_code,
                "error_category": row.error_category,
                "error_message": row.error_message,
                "checked_at": row.checked_at,
                "source_pages": [ref.source_page_url for ref in sources.get(row.target_url, [])],
            }
            for row in failed_links
        ],
        failed_pages=[
            {
                "url": row.url,
                "depth": row.depth,
                "status_code": row.status_code,
                "error_message": row.error_message,
                "created_at": row.created_at,
            }
            for row in failed_pages
        ],
        links={
            "job": _path_for(request, "dashboard_results_job", job_id=job_id),
            "run_json": _path_for(request, "api_results_run_detail", job_id=job_id, run_id=run_id),
            "jobs_index": _path_for(request, "dashboard_results_jobs"),
            "refresh": _path_for(request, "dashboard_results_run", job_id=job_id, run_id=run_id),
        },
    )
    return HTMLResponse(page)


def _should_ignore_event_callback(payload: dict[str, Any]) -> bool:
    inner = payload.get("event")
    if not isinstance(inner, dict):
        return False
    if inner.get("type") == "message":
        if inner.get("bot_id"):
            return True
        if inner.get("subtype") in ("message_changed", "message_deleted", "channel_join", "channel_leave"):
            return True
    return False


async def slack_job_event(request: Request) -> Response:
    job_slug = request.path_params["job_slug"]
    if not _SLUG_RE.fullmatch(job_slug):
        return _json_error(404, "invalid_slug")

    jobs_root: Path = request.app.state.jobs_root
    jobs_resolved = jobs_root.resolve()
    job_path = (jobs_resolved / f"{job_slug}.job.json").resolve()
    try:
        job_path.relative_to(jobs_resolved)
    except ValueError:
        return _json_error(404, "invalid_path")

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return _json_error(400, "invalid_json")

    if not isinstance(payload, dict):
        return _json_error(400, "invalid_payload")

    if not job_path.is_file():
        return _json_error(404, "job_not_found")

    try:
        config = load_effective_job_config(job_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return _json_error(404, "job_load_failed")

    issues = validate_job_config(config)
    if issues:
        return _json_error(500, "invalid_config")

    secret = resolve_notifications_signing_secret(config)
    if not secret:
        return _json_error(500, "signing_secret_unconfigured")

    ts = request.headers.get("x-slack-request-timestamp") or ""
    sig = request.headers.get("x-slack-signature")
    if not verify_slack_signing_secret(
        signing_secret=secret,
        request_timestamp=ts,
        raw_body=raw_body,
        signature_header=sig,
    ):
        return _json_error(401, "invalid_signature")

    envelope_type = str(payload.get("type") or "")
    if envelope_type == "url_verification":
        challenge = payload.get("challenge")
        if not challenge or not isinstance(challenge, str):
            return _json_error(400, "missing_challenge")
        return JSONResponse({"challenge": challenge})

    if envelope_type != "event_callback":
        return _json_error(400, "unsupported_type")

    if _should_ignore_event_callback(payload):
        return JSONResponse({"ok": True, "ignored": True})

    return _process_event_callback(config, payload)


def _load_and_validate_job_config(job_path: Path) -> tuple[JobConfig | None, str | None]:
    try:
        config = load_effective_job_config(job_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None, "job_load_failed"
    issues = validate_job_config(config)
    if issues:
        return None, "invalid_config"
    return config, None


def _collect_channel_routes(jobs_root: Path) -> tuple[dict[str, Path], str]:
    """Build channel_id -> job_path map and return unified signing secret env name."""
    routes: dict[str, Path] = {}
    signing_env_names: set[str] = set()
    for job_path in sorted(jobs_root.glob("*.job.json")):
        config, err = _load_and_validate_job_config(job_path)
        if config is None:
            raise RuntimeError(f"Failed loading job config for routing: {job_path} ({err})")
        notifications = config["notifications"]
        signing_env_names.add(notifications_signing_secret_env_name(config))
        if not config["meta"]["enabled"] or not notifications["enabled"]:
            continue
        for destination in notifications["destinations"]:
            if destination["type"] != "slack" or not destination["enabled"]:
                continue
            channel_id = str(destination.get("channel_id") or "").strip()
            if not channel_id:
                continue
            existing = routes.get(channel_id)
            if existing is not None and existing != job_path:
                raise RuntimeError(
                    f"Duplicate Slack channel mapping for {channel_id}: {existing.name} and {job_path.name}"
                )
            routes[channel_id] = job_path
    if not signing_env_names:
        signing_env_names.add("BLINK_SLACK_SIGNING_SECRET")
    if len(signing_env_names) > 1:
        names = ", ".join(sorted(signing_env_names))
        raise RuntimeError(f"Conflicting slack_signing_secret_env across jobs: {names}")
    return routes, next(iter(signing_env_names))


def _verify_signature_with_env(raw_body: bytes, payload: dict[str, Any], env_name: str, request: Request) -> Response | None:
    _ = payload
    secret_raw = os.getenv(env_name)
    secret = secret_raw.strip() if secret_raw else ""
    if not secret:
        return _json_error(500, "signing_secret_unconfigured")
    ts = request.headers.get("x-slack-request-timestamp") or ""
    sig = request.headers.get("x-slack-signature")
    if not verify_slack_signing_secret(
        signing_secret=secret,
        request_timestamp=ts,
        raw_body=raw_body,
        signature_header=sig,
    ):
        return _json_error(401, "invalid_signature")
    return None


async def slack_events(request: Request) -> Response:
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return _json_error(400, "invalid_json")
    if not isinstance(payload, dict):
        return _json_error(400, "invalid_payload")

    verify_err = _verify_signature_with_env(raw_body, payload, request.app.state.signing_secret_env_name, request)
    if verify_err is not None:
        return verify_err

    envelope_type = str(payload.get("type") or "")
    if envelope_type == "url_verification":
        challenge = payload.get("challenge")
        if not challenge or not isinstance(challenge, str):
            return _json_error(400, "missing_challenge")
        return JSONResponse({"challenge": challenge})
    if envelope_type != "event_callback":
        return _json_error(400, "unsupported_type")
    if _should_ignore_event_callback(payload):
        return JSONResponse({"ok": True, "ignored": True})

    channel_id = extract_slack_channel_id(payload).strip()
    if not channel_id:
        return JSONResponse({"ok": True, "ignored": True, "reason": "missing_channel"})
    job_path = request.app.state.channel_routes.get(channel_id)
    if job_path is None:
        return JSONResponse({"ok": True, "ignored": True, "reason": "unmapped_channel"})

    config, err = _load_and_validate_job_config(job_path)
    if config is None:
        return _json_error(500, err or "job_load_failed")
    return _process_event_callback(config, payload)


def _process_event_callback(config: JobConfig, payload: dict[str, Any]) -> JSONResponse:
    paths = build_job_paths(config["meta"]["job_id"])
    db_path = paths.db_path
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repository = CrawlRepository(connection)
    try:
        ok, err = apply_inbound_slack_from_envelope(config, repository, payload)
        if not ok:
            if err == "unparsed":
                return JSONResponse({"ok": True, "ignored": True})
            return JSONResponse({"ok": False, "error": err or "apply_failed"}, status_code=500)
        return JSONResponse({"ok": True})
    finally:
        connection.close()


def build_app(*, jobs_root: Path | None = None, enable_scheduler: bool = False) -> Starlette:
    root = (jobs_root if jobs_root is not None else project_root() / "jobs").resolve()
    routes, signing_env_name = _collect_channel_routes(root)
    scheduler_service = BlinkSchedulerService(root)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        _ = app
        if enable_scheduler:
            scheduler_service.start()
        yield
        if enable_scheduler:
            scheduler_service.shutdown(wait=True)

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"], name="health"),
            Route("/notifications/slack/health", health, methods=["GET"], name="slack_health"),
            Route("/api/schedule", api_schedule, methods=["GET"], name="api_schedule"),
            Route("/api/results/jobs", api_results_jobs, methods=["GET"], name="api_results_jobs"),
            Route(
                "/api/results/jobs/{job_id}/runs",
                api_results_job_runs,
                methods=["GET"],
                name="api_results_job_runs",
            ),
            Route(
                "/api/results/jobs/{job_id}/runs/{run_id:int}",
                api_results_run_detail,
                methods=["GET"],
                name="api_results_run_detail",
            ),
            Route("/dashboard", schedule_dashboard, methods=["GET"], name="dashboard_schedule"),
            Route("/dashboard/results", dashboard_results_jobs, methods=["GET"], name="dashboard_results_jobs"),
            Route(
                "/dashboard/results/{job_id}",
                dashboard_results_job,
                methods=["GET"],
                name="dashboard_results_job",
            ),
            Route(
                "/dashboard/results/{job_id}/runs/{run_id:int}",
                dashboard_results_run,
                methods=["GET"],
                name="dashboard_results_run",
            ),
            Route("/notifications/slack/events", slack_events, methods=["POST"], name="slack_events"),
            Route("/notifications/slack/job/{job_slug}", slack_job_event, methods=["POST"], name="slack_job_event"),
        ],
        lifespan=lifespan,
    )
    app.state.jobs_root = root
    app.state.channel_routes = routes
    app.state.signing_secret_env_name = signing_env_name
    app.state.scheduler_service = scheduler_service
    app.state.enable_scheduler = enable_scheduler
    return app
