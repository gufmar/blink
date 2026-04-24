"""Starlette ASGI application for Slack Events API."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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


def _normalize_base_path(value: str | None) -> str:
    if not value:
        return ""
    trimmed = value.strip().strip("/")
    return f"/{trimmed}" if trimmed else ""


def _join_url_paths(*parts: str) -> str:
    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        chunk = str(part).strip("/")
        if chunk:
            cleaned.append(chunk)
    if not cleaned:
        return "/"
    return "/" + "/".join(cleaned)


def _path_for(request: Request, route_name: str, **path_params: object) -> str:
    app_path = str(request.app.url_path_for(route_name, **path_params))
    root_path = _normalize_base_path(str(request.scope.get("root_path") or ""))
    config_base = _normalize_base_path(getattr(request.app.state, "route_base_path", ""))
    if config_base and root_path.startswith(config_base):
        config_base = ""
    return _join_url_paths(config_base, root_path, app_path)


def _query_list(request: Request, key: str) -> list[str]:
    values: list[str] = []
    for raw in request.query_params.getlist(key):
        for part in str(raw).split(","):
            v = part.strip()
            if v:
                values.append(v)
    # Preserve order while deduping.
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            out.append(v)
            seen.add(v)
    return out


def _is_ignored_link_check_result(row: Any) -> bool:
    return str(getattr(row, "decision_state", "") or "") == "ignored"


def _split_failed_and_ignored_link_results(rows: list[Any]) -> tuple[list[Any], list[Any]]:
    failed_rows: list[Any] = []
    ignored_rows: list[Any] = []
    for row in rows:
        if _is_ignored_link_check_result(row):
            ignored_rows.append(row)
        else:
            failed_rows.append(row)
    return failed_rows, ignored_rows


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
        include_status = _query_list(request, "include_status")
        exclude_status = _query_list(request, "exclude_status")
        include_category = _query_list(request, "include_category")
        exclude_category = _query_list(request, "exclude_category")

        failed_links_all = repo.list_latest_failed_link_check_results(run_id, limit=2000)
        active_failed_all, ignored_failed_all = _split_failed_and_ignored_link_results(failed_links_all)
        failed_links: list[Any] = []
        for row in active_failed_all:
            status_value = "none" if row.status_code is None else str(row.status_code)
            category_value = str(row.error_category or "uncategorized")
            if include_status and status_value not in include_status:
                continue
            if status_value in exclude_status:
                continue
            if include_category and category_value not in include_category:
                continue
            if category_value in exclude_category:
                continue
            failed_links.append(row)

        failed_targets = [row.target_url for row in failed_links]
        ignored_targets = [row.target_url for row in ignored_failed_all]
        sources = repo.list_source_page_refs_for_targets(run_id, [*failed_targets, *ignored_targets])
        failed_pages = repo.list_crawled_pages(run_id, only_failed=True, limit=200)
        counts = repo.get_distinct_link_counts()
        category_counts: dict[str, int] = {}
        for row in active_failed_all:
            key = str(row.error_category or "uncategorized")
            category_counts[key] = category_counts.get(key, 0) + 1
        history = repo.list_run_history(job_id, limit=50)
        idx = next((i for i, rec in enumerate(history) if rec.run_id == run_id), None)
        previous_run_ids = [history[i].run_id for i in range((idx or 0) + 1, min((idx or 0) + 3, len(history)))]
        per_run_counts: dict[int, dict[str, int]] = {run_id: category_counts}
        for prev_run_id in previous_run_ids:
            prev_rows = repo.list_latest_failed_link_check_results(prev_run_id, limit=2000)
            prev_active, _prev_ignored = _split_failed_and_ignored_link_results(prev_rows)
            prev_counts: dict[str, int] = {}
            for row in prev_active:
                key = str(row.error_category or "uncategorized")
                prev_counts[key] = prev_counts.get(key, 0) + 1
            per_run_counts[prev_run_id] = prev_counts
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
                "job_totals": {
                    "pages_total": counts["internal_urls_distinct"],
                    "external_links_total": counts["external_urls_distinct"],
                },
                "failed_overview": {
                    "failed_total": len(active_failed_all),
                    "ignored_total": len(ignored_failed_all),
                    "by_category": category_counts,
                    "per_run_category_counts": per_run_counts,
                    "comparison_run_ids": [run_id, *previous_run_ids],
                },
                "filters": {
                    "include_status": include_status,
                    "exclude_status": exclude_status,
                    "include_category": include_category,
                    "exclude_category": exclude_category,
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
                "ignored_links": [
                    {
                        "target_url": row.target_url,
                        "status_code": row.status_code,
                        "error_category": row.error_category,
                        "error_message": row.error_message,
                        "checked_at": row.checked_at,
                        "decision_reason": row.decision_reason,
                        "source_pages": [ref.source_page_url for ref in sources.get(row.target_url, [])],
                    }
                    for row in ignored_failed_all
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
        include_status = _query_list(request, "include_status")
        exclude_status = _query_list(request, "exclude_status")
        include_category = _query_list(request, "include_category")
        exclude_category = _query_list(request, "exclude_category")
        failed_links_all = repo.list_latest_failed_link_check_results(run_id, limit=2000)
        active_failed_all, ignored_failed_all = _split_failed_and_ignored_link_results(failed_links_all)
        failed_links: list[Any] = []
        for row in active_failed_all:
            status_value = "none" if row.status_code is None else str(row.status_code)
            category_value = str(row.error_category or "uncategorized")
            if include_status and status_value not in include_status:
                continue
            if status_value in exclude_status:
                continue
            if include_category and category_value not in include_category:
                continue
            if category_value in exclude_category:
                continue
            failed_links.append(row)
        failed_pages = repo.list_crawled_pages(run_id, only_failed=True, limit=200)
        failed_targets = [row.target_url for row in failed_links]
        ignored_targets = [row.target_url for row in ignored_failed_all]
        sources = repo.list_source_page_refs_for_targets(run_id, [*failed_targets, *ignored_targets])
        counts = repo.get_distinct_link_counts()
        category_counts: dict[str, int] = {}
        status_options: set[str] = set()
        category_options: set[str] = set()
        for row in active_failed_all:
            category_key = str(row.error_category or "uncategorized")
            category_counts[category_key] = category_counts.get(category_key, 0) + 1
            status_options.add("none" if row.status_code is None else str(row.status_code))
            category_options.add(category_key)
        history = repo.list_run_history(job_id, limit=50)
        idx = next((i for i, rec in enumerate(history) if rec.run_id == run_id), None)
        comparison_run_ids = [run_id]
        if idx is not None:
            for i in range(idx + 1, min(idx + 3, len(history))):
                comparison_run_ids.append(history[i].run_id)
        per_run_counts: dict[int, dict[str, int]] = {run_id: dict(category_counts)}
        for prev_run_id in comparison_run_ids[1:]:
            prev_rows = repo.list_latest_failed_link_check_results(prev_run_id, limit=2000)
            prev_active, _prev_ignored = _split_failed_and_ignored_link_results(prev_rows)
            prev_counts: dict[str, int] = {}
            for row in prev_active:
                key = str(row.error_category or "uncategorized")
                prev_counts[key] = prev_counts.get(key, 0) + 1
            per_run_counts[prev_run_id] = prev_counts
    finally:
        _close_repo(repo_and_conn)

    def with_filters(
        path: str,
        *,
        include_status_q: list[str],
        exclude_status_q: list[str],
        include_category_q: list[str],
        exclude_category_q: list[str],
    ) -> str:
        query_items: list[tuple[str, str]] = []
        for v in include_status_q:
            query_items.append(("include_status", v))
        for v in exclude_status_q:
            query_items.append(("exclude_status", v))
        for v in include_category_q:
            query_items.append(("include_category", v))
        for v in exclude_category_q:
            query_items.append(("exclude_category", v))
        if not query_items:
            return path
        return f"{path}?{urlencode(query_items)}"

    base_run_path = _path_for(request, "dashboard_results_run", job_id=job_id, run_id=run_id)
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
        totals={
            "pages_total": counts["internal_urls_distinct"],
            "external_links_total": counts["external_urls_distinct"],
        },
        failed_summary={
            "failed_total": len(active_failed_all),
            "ignored_total": len(ignored_failed_all),
            "by_category": category_counts,
            "per_run_category_counts": per_run_counts,
            "comparison_run_ids": comparison_run_ids,
        },
        filters={
            "include_status": include_status,
            "exclude_status": exclude_status,
            "include_category": include_category,
            "exclude_category": exclude_category,
            "status_options": sorted(status_options),
            "category_options": sorted(category_options),
            "filter_action": base_run_path,
            "clear_filters_url": base_run_path,
        },
        failed_links=[
            {
                "target_url": row.target_url,
                "status_code": row.status_code,
                "error_category": row.error_category,
                "error_message": row.error_message,
                "checked_at": row.checked_at,
                "source_pages": [ref.source_page_url for ref in sources.get(row.target_url, [])],
                "target_href": row.target_url,
                "source_page_hrefs": [ref.source_page_url for ref in sources.get(row.target_url, [])],
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
        ignored_links=[
            {
                "target_url": row.target_url,
                "target_href": row.target_url,
                "status_code": row.status_code,
                "error_category": row.error_category,
                "error_message": row.error_message,
                "checked_at": row.checked_at,
                "decision_reason": row.decision_reason,
                "source_pages": [ref.source_page_url for ref in sources.get(row.target_url, [])],
                "source_page_hrefs": [ref.source_page_url for ref in sources.get(row.target_url, [])],
            }
            for row in ignored_failed_all
        ],
        links={
            "job": _path_for(request, "dashboard_results_job", job_id=job_id),
            "run_json": with_filters(
                _path_for(request, "api_results_run_detail", job_id=job_id, run_id=run_id),
                include_status_q=include_status,
                exclude_status_q=exclude_status,
                include_category_q=include_category,
                exclude_category_q=exclude_category,
            ),
            "jobs_index": _path_for(request, "dashboard_results_jobs"),
            "refresh": with_filters(
                base_run_path,
                include_status_q=include_status,
                exclude_status_q=exclude_status,
                include_category_q=include_category,
                exclude_category_q=exclude_category,
            ),
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


def build_app(
    *,
    jobs_root: Path | None = None,
    enable_scheduler: bool = False,
    route_base_path: str = "",
) -> Starlette:
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
    app.state.route_base_path = _normalize_base_path(route_base_path)
    return app
