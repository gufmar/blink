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
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

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
    render_job_task_history_html,
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


def _branding_links(request: Request) -> dict[str, str]:
    """Resolve optional logo/favicon static links for dashboard pages."""
    static_root = Path(__file__).resolve().parent / "static" / "branding"

    def static_url(rel_path: str) -> str:
        return _path_for(request, "static", path=f"branding/{rel_path}")

    def pick_existing(paths: list[str]) -> str:
        for rel in paths:
            if (static_root / rel).is_file():
                return static_url(rel)
        return ""

    logo_url = pick_existing(
        [
            "logo/blink-logo.svg",
            "logo/blink-logo.png",
            "logo/logo.svg",
            "logo/logo.png",
            "logo/blink-512x512.png",
        ]
    )
    if not logo_url:
        for candidate in sorted((static_root / "logo").glob("*")):
            if candidate.is_file() and candidate.suffix.lower() in {".png", ".svg", ".jpg", ".jpeg", ".webp"}:
                logo_url = static_url(f"logo/{candidate.name}")
                break

    return {
        "logo_url": logo_url,
        "favicon_ico": pick_existing(["favicon/favicon.ico"]),
        "favicon_svg": pick_existing(["favicon/favicon.svg"]),
        "apple_touch_icon": pick_existing(["favicon/apple-touch-icon.png"]),
        "manifest": pick_existing(["favicon/site.webmanifest"]),
    }


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


def _resolve_include_filters(
    request: Request,
    *,
    status_options: set[str],
    category_options: set[str],
) -> tuple[list[str], list[str]]:
    include_status = _query_list(request, "include_status")
    include_category = _query_list(request, "include_category")
    if not include_status:
        include_status = sorted(status_options)
    if not include_category:
        include_category = sorted(category_options)
    return include_status, include_category


def _row_status_value(row: Any) -> str:
    status = getattr(row, "status_code", None)
    return "none" if status is None else str(status)


def _row_category_value(row: Any) -> str:
    return str(getattr(row, "error_category", None) or "uncategorized")


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
    return jobs_root / "data" / job_id / "db" / f"{job_id}.sqlite3"


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


def _serialize_link_check_history(job_id: str, repo: CrawlRepository | None, *, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        runs = repo.list_link_check_run_history(job_id, limit=limit)
    except sqlite3.Error:
        return []
    return [
        {
            "run_id": rec.run_id,
            "started_at": rec.started_at,
            "finished_at": rec.finished_at,
            "based_on_crawl_run_id": rec.based_on_crawl_run_id,
            "checked_total": rec.checked_total,
            "passed_total": rec.passed_total,
            "failed_total": rec.failed_total,
            "errored_total": rec.errored_total,
            "ignored_total": rec.ignored_total,
        }
        for rec in runs
    ]


async def schedule_dashboard(request: Request) -> HTMLResponse:
    svc: BlinkSchedulerService = request.app.state.scheduler_service
    payload = svc.build_schedule_payload()
    jobs_root: Path = request.app.state.jobs_root
    jobs = _load_job_entries(jobs_root)
    job_meta: dict[str, dict[str, Any]] = {}
    for job in jobs:
        job_id = str(job["job_id"])
        repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
        repo = repo_and_conn[0] if repo_and_conn else None
        try:
            crawl_history = _serialize_run_history(job_id, repo, limit=1)
            link_history = _serialize_link_check_history(job_id, repo, limit=1)
            counts = repo.get_distinct_link_counts() if repo is not None else {"internal_urls_distinct": 0, "external_urls_distinct": 0}
        finally:
            _close_repo(repo_and_conn)
        latest_crawl = crawl_history[0] if crawl_history else None
        latest_link = link_history[0] if link_history else None
        crawl_history_url = _path_for(request, "dashboard_results_job_history", job_id=job_id)
        latest_crawl_url = (
            f"{_path_for(request, 'dashboard_results_run', job_id=job_id, run_id=int(latest_crawl['run_id']))}?task_type=crawl"
            if latest_crawl and latest_crawl.get("run_id") is not None
            else ""
        )
        latest_link_url = (
            f"{_path_for(request, 'dashboard_results_run', job_id=job_id, run_id=int(latest_link['run_id']))}?task_type=link_check"
            if latest_link and latest_link.get("run_id") is not None
            else ""
        )
        external_total = int(counts.get("external_urls_distinct") or 0)
        failed_total = int(latest_link.get("failed_total") or 0) if latest_link else 0
        failed_ratio = int(round((failed_total / external_total) * 100.0)) if external_total > 0 else 0
        ignored_total = int(latest_link.get("ignored_total") or 0) if latest_link else 0
        ignored_ratio = int(round((ignored_total / external_total) * 100.0)) if external_total > 0 else 0
        job_meta[job_id] = {
            "job_name": str(job.get("name") or job_id),
            "history_url": crawl_history_url,
            "latest_crawl_url": latest_crawl_url,
            "latest_link_url": latest_link_url,
            "latest_crawl": latest_crawl,
            "latest_link": latest_link,
            "pages_total": int(counts.get("internal_urls_distinct") or 0),
            "external_total": external_total,
            "failed_total": failed_total,
            "failed_ratio": failed_ratio,
            "ignored_total": ignored_total,
            "ignored_ratio": ignored_ratio,
        }
    for task in payload.get("tasks") or []:
        job_id = str(task.get("job_id") or "")
        meta = job_meta.get(job_id) or {}
        task.update(meta)
    page = render_schedule_dashboard_html(
        payload,
        links={
            "health": _path_for(request, "health"),
            "schedule_json": _path_for(request, "api_schedule"),
            "slack_health": _path_for(request, "slack_health"),
            "results_index": _path_for(request, "dashboard_results_jobs"),
            "schedule_refresh": _path_for(request, "dashboard_schedule"),
            **_branding_links(request),
        },
    )
    return HTMLResponse(page)


async def dashboard_results_job_history(request: Request) -> HTMLResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return HTMLResponse("Job not found", status_code=404)
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    repo = repo_and_conn[0] if repo_and_conn else None
    try:
        crawl_runs = _serialize_run_history(job_id, repo, limit=200)
        link_runs = _serialize_link_check_history(job_id, repo, limit=400)
    finally:
        _close_repo(repo_and_conn)
    by_crawl_id: dict[int, list[dict[str, Any]]] = {}
    for row in link_runs:
        crawl_run_id = int(row.get("based_on_crawl_run_id") or 0)
        by_crawl_id.setdefault(crawl_run_id, []).append(row)
    for crawl in crawl_runs:
        crawl_id = int(crawl.get("run_id") or 0)
        crawl["details_url"] = (
            f"{_path_for(request, 'dashboard_results_run', job_id=job_id, run_id=crawl_id)}?task_type=crawl"
        )
        related = by_crawl_id.get(crawl_id, [])
        for rel in related:
            rel["details_url"] = (
                f"{_path_for(request, 'dashboard_results_run', job_id=job_id, run_id=int(rel['run_id']))}"
                "?task_type=link_check"
            )
        crawl["link_checks"] = sorted(related, key=lambda r: str(r.get("started_at") or ""), reverse=True)
    page = render_job_task_history_html(
        job=job,
        crawl_runs=crawl_runs,
        links={
            "main_dashboard": _path_for(request, "dashboard_schedule"),
            "jobs_index": _path_for(request, "dashboard_results_jobs"),
            "refresh": _path_for(request, "dashboard_results_job_history", job_id=job_id),
            **_branding_links(request),
        },
    )
    return HTMLResponse(page)


async def api_results_jobs(request: Request) -> JSONResponse:
    jobs_root: Path = request.app.state.jobs_root
    jobs = _load_job_entries(jobs_root)
    out: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    for job in jobs:
        repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, str(job["job_id"])))
        repo = repo_and_conn[0] if repo_and_conn else None
        try:
            crawl_history = _serialize_run_history(str(job["job_id"]), repo, limit=1)
            link_check_history = _serialize_link_check_history(str(job["job_id"]), repo, limit=1)
            out.append(
                {
                    **job,
                    "latest_run": crawl_history[0] if crawl_history else None,
                }
            )
            task_rows.append(
                {
                    **job,
                    "task_type": "crawl",
                    "latest_run": crawl_history[0] if crawl_history else None,
                }
            )
            task_rows.append(
                {
                    **job,
                    "task_type": "link_check",
                    "latest_run": link_check_history[0] if link_check_history else None,
                }
            )
        finally:
            _close_repo(repo_and_conn)
    return JSONResponse({"jobs": out, "task_rows": task_rows})


async def api_results_job_runs(request: Request) -> JSONResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return _json_error(404, "job_not_found")
    task_type = str(request.query_params.get("task_type") or "all").strip().lower()
    if task_type not in {"all", "crawl", "link_check"}:
        return _json_error(400, "invalid_task_type")
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    repo = repo_and_conn[0] if repo_and_conn else None
    try:
        if task_type == "link_check":
            runs = _serialize_link_check_history(job_id, repo, limit=100)
        elif task_type == "crawl":
            runs = _serialize_run_history(job_id, repo, limit=100)
        else:
            crawl_runs = _serialize_run_history(job_id, repo, limit=100)
            for row in crawl_runs:
                row["task_type"] = "crawl"
            link_runs = _serialize_link_check_history(job_id, repo, limit=100)
            for row in link_runs:
                row["task_type"] = "link_check"
            runs = [*crawl_runs, *link_runs]
            runs.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    finally:
        _close_repo(repo_and_conn)
    return JSONResponse({"job": job, "task_type": task_type, "runs": runs})


async def api_results_run_detail(request: Request) -> JSONResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    run_id = int(request.path_params["run_id"])
    task_type = str(request.query_params.get("task_type") or "crawl").strip().lower()
    if task_type not in {"crawl", "link_check"}:
        return _json_error(400, "invalid_task_type")
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return _json_error(404, "job_not_found")
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    if repo_and_conn is None:
        return _json_error(404, "run_not_found")
    repo, _ = repo_and_conn
    try:
        if task_type == "link_check":
            link_check_run = repo.get_link_check_run(run_id)
            if link_check_run is None or link_check_run.job_id != job_id:
                return _json_error(404, "run_not_found")
            run = repo.get_run_record(job_id=job_id, run_id=link_check_run.based_on_crawl_run_id)
            if run is None:
                return _json_error(404, "run_not_found")
            selected_crawl_run_id = link_check_run.based_on_crawl_run_id
            selected_link_check_run_id = link_check_run.run_id
        else:
            run = repo.get_run_record(job_id=job_id, run_id=run_id)
            if run is None:
                return _json_error(404, "run_not_found")
            selected_crawl_run_id = run.run_id
            selected_link_check_run_id = None
        failed_links_all = repo.list_latest_failed_link_check_results(
            selected_crawl_run_id,
            link_check_run_id=selected_link_check_run_id,
            limit=2000,
        )
        active_failed_all, ignored_failed_all = _split_failed_and_ignored_link_results(failed_links_all)
        failed_pages_all = repo.list_crawled_pages(selected_crawl_run_id, only_failed=True, limit=200)
        status_options = {
            *(_row_status_value(row) for row in active_failed_all),
            *(_row_status_value(row) for row in ignored_failed_all),
            *(_row_status_value(row) for row in failed_pages_all),
        }
        category_options = {
            *(_row_category_value(row) for row in active_failed_all),
            *(_row_category_value(row) for row in ignored_failed_all),
        }
        include_status, include_category = _resolve_include_filters(
            request,
            status_options=status_options,
            category_options=category_options,
        )
        failed_links: list[Any] = []
        for row in active_failed_all:
            status_value = "none" if row.status_code is None else str(row.status_code)
            category_value = str(row.error_category or "uncategorized")
            if include_status and status_value not in include_status:
                continue
            if include_category and category_value not in include_category:
                continue
            failed_links.append(row)
        ignored_links: list[Any] = []
        for row in ignored_failed_all:
            status_value = _row_status_value(row)
            category_value = _row_category_value(row)
            if include_status and status_value not in include_status:
                continue
            if include_category and category_value not in include_category:
                continue
            ignored_links.append(row)
        failed_pages = [
            row
            for row in failed_pages_all
            if (not include_status or _row_status_value(row) in include_status)
        ]

        failed_targets = [row.target_url for row in failed_links]
        ignored_targets = [row.target_url for row in ignored_failed_all]
        sources = repo.list_source_page_refs_for_targets(selected_crawl_run_id, [*failed_targets, *ignored_targets])
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
                    "task_type": task_type,
                    "link_check_run_id": (selected_link_check_run_id if task_type == "link_check" else None),
                    "based_on_crawl_run_id": selected_crawl_run_id,
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
                    "include_category": include_category,
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
                    for row in ignored_links
                ],
            }
        )
    finally:
        _close_repo(repo_and_conn)


async def dashboard_results_jobs(request: Request) -> HTMLResponse:
    jobs_root: Path = request.app.state.jobs_root
    jobs = _load_job_entries(jobs_root)
    rows: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job["job_id"])
        repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
        repo = repo_and_conn[0] if repo_and_conn else None
        try:
            crawl_history = _serialize_run_history(job_id, repo, limit=1)
            link_history = _serialize_link_check_history(job_id, repo, limit=1)
        finally:
            _close_repo(repo_and_conn)
        latest_crawl = crawl_history[0] if crawl_history else None
        latest_link = link_history[0] if link_history else None
        row = {
            **job,
            "latest_crawl": latest_crawl,
            "latest_link_check": latest_link,
            "crawl_history_url": _path_for(request, "dashboard_results_job_history", job_id=job_id),
            "latest_link_check_url": (
                f"{_path_for(request, 'dashboard_results_run', job_id=job_id, run_id=int(latest_link['run_id']))}?task_type=link_check"
                if latest_link and latest_link.get("run_id") is not None
                else ""
            ),
        }
        rows.append(row)
    page = render_results_jobs_html(
        {"jobs_summary": rows},
        links={
            "main_dashboard": _path_for(request, "dashboard_schedule"),
            "schedule": _path_for(request, "dashboard_schedule"),
            "jobs_json": _path_for(request, "api_results_jobs"),
            "health": _path_for(request, "health"),
            "refresh": _path_for(request, "dashboard_results_jobs"),
            **_branding_links(request),
        },
    )
    return HTMLResponse(page)


async def dashboard_results_job(request: Request) -> HTMLResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return HTMLResponse("Job not found", status_code=404)
    show_crawl = str(request.query_params.get("show_crawl") or "1").lower() not in {"0", "false", "no"}
    show_link_check = str(request.query_params.get("show_link_check") or "1").lower() not in {"0", "false", "no"}
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    repo = repo_and_conn[0] if repo_and_conn else None
    try:
        counts = repo.get_distinct_link_counts() if repo is not None else {"external_urls_distinct": 0}
        total_external_urls = int(counts.get("external_urls_distinct") or 0)
        crawl_rows = _serialize_run_history(job_id, repo, limit=200)
        for row in crawl_rows:
            row["task_type"] = "crawl"
            row["total_external_urls"] = total_external_urls
        link_rows = _serialize_link_check_history(job_id, repo, limit=200)
        for row in link_rows:
            row["task_type"] = "link_check"
            row["total_external_urls"] = total_external_urls
    finally:
        _close_repo(repo_and_conn)
    run_rows = []
    if show_crawl:
        run_rows.extend(crawl_rows)
    if show_link_check:
        run_rows.extend(link_rows)
    run_rows.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    for run in run_rows:
        row_task_type = str(run.get("task_type") or "crawl")
        base_details_url = _path_for(
            request,
            "dashboard_results_run",
            job_id=job_id,
            run_id=int(run["run_id"]),
        )
        run["details_url"] = f"{base_details_url}?task_type={row_task_type}"
    runs_json_url = (
        f"{_path_for(request, 'api_results_job_runs', job_id=job_id)}"
        f"?task_type=all&show_crawl={1 if show_crawl else 0}&show_link_check={1 if show_link_check else 0}"
    )
    refresh_url = (
        f"{_path_for(request, 'dashboard_results_job', job_id=job_id)}"
        f"?show_crawl={1 if show_crawl else 0}&show_link_check={1 if show_link_check else 0}"
    )
    page = render_results_job_html(
        job=job,
        task_type="all",
        run_rows=run_rows,
        links={
            "main_dashboard": _path_for(request, "dashboard_schedule"),
            "jobs_index": _path_for(request, "dashboard_results_job_history", job_id=job_id),
            "runs_json": runs_json_url,
            "schedule": _path_for(request, "dashboard_schedule"),
            "refresh": refresh_url,
            "show_crawl_url": (
                f"{_path_for(request, 'dashboard_results_job', job_id=job_id)}"
                f"?show_crawl={0 if show_crawl else 1}&show_link_check={1 if show_link_check else 0}"
            ),
            "show_link_check_url": (
                f"{_path_for(request, 'dashboard_results_job', job_id=job_id)}"
                f"?show_crawl={1 if show_crawl else 0}&show_link_check={0 if show_link_check else 1}"
            ),
            "show_crawl": show_crawl,
            "show_link_check": show_link_check,
            **_branding_links(request),
        },
    )
    return HTMLResponse(page)


async def dashboard_results_run(request: Request) -> HTMLResponse:
    jobs_root: Path = request.app.state.jobs_root
    job_id = str(request.path_params["job_id"])
    run_id = int(request.path_params["run_id"])
    task_type = str(request.query_params.get("task_type") or "crawl").strip().lower()
    if task_type not in {"crawl", "link_check"}:
        task_type = "crawl"
    job = _job_entry_by_id(jobs_root, job_id)
    if job is None:
        return HTMLResponse("Job not found", status_code=404)
    repo_and_conn = _open_repo_if_exists(_db_path_for_job(jobs_root, job_id))
    if repo_and_conn is None:
        return HTMLResponse("Run not found", status_code=404)
    repo, _ = repo_and_conn
    try:
        if task_type == "link_check":
            link_check_run = repo.get_link_check_run(run_id)
            if link_check_run is None or link_check_run.job_id != job_id:
                return HTMLResponse("Run not found", status_code=404)
            run = repo.get_run_record(job_id=job_id, run_id=link_check_run.based_on_crawl_run_id)
            if run is None:
                return HTMLResponse("Run not found", status_code=404)
            selected_crawl_run_id = link_check_run.based_on_crawl_run_id
            selected_link_check_run_id = link_check_run.run_id
        else:
            run = repo.get_run_record(job_id=job_id, run_id=run_id)
            if run is None:
                return HTMLResponse("Run not found", status_code=404)
            selected_crawl_run_id = run.run_id
            selected_link_check_run_id = None
        failed_links_all = repo.list_latest_failed_link_check_results(
            selected_crawl_run_id,
            link_check_run_id=selected_link_check_run_id,
            limit=2000,
        )
        active_failed_all, ignored_failed_all = _split_failed_and_ignored_link_results(failed_links_all)
        failed_pages_all = repo.list_crawled_pages(selected_crawl_run_id, only_failed=True, limit=200)
        status_options = {
            *(_row_status_value(row) for row in active_failed_all),
            *(_row_status_value(row) for row in ignored_failed_all),
            *(_row_status_value(row) for row in failed_pages_all),
        }
        category_options = {
            *(_row_category_value(row) for row in active_failed_all),
            *(_row_category_value(row) for row in ignored_failed_all),
        }
        include_status, include_category = _resolve_include_filters(
            request,
            status_options=status_options,
            category_options=category_options,
        )
        failed_links: list[Any] = []
        for row in active_failed_all:
            status_value = "none" if row.status_code is None else str(row.status_code)
            category_value = str(row.error_category or "uncategorized")
            if include_status and status_value not in include_status:
                continue
            if include_category and category_value not in include_category:
                continue
            failed_links.append(row)
        ignored_links: list[Any] = []
        for row in ignored_failed_all:
            status_value = _row_status_value(row)
            category_value = _row_category_value(row)
            if include_status and status_value not in include_status:
                continue
            if include_category and category_value not in include_category:
                continue
            ignored_links.append(row)
        failed_pages = [
            row
            for row in failed_pages_all
            if (not include_status or _row_status_value(row) in include_status)
        ]
        failed_targets = [row.target_url for row in failed_links]
        ignored_targets = [row.target_url for row in ignored_links]
        sources = repo.list_source_page_refs_for_targets(selected_crawl_run_id, [*failed_targets, *ignored_targets])
        counts = repo.get_distinct_link_counts()
        category_counts: dict[str, int] = {}
        for row in active_failed_all:
            category_key = str(row.error_category or "uncategorized")
            category_counts[category_key] = category_counts.get(category_key, 0) + 1
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
        include_category_q: list[str],
    ) -> str:
        query_items: list[tuple[str, str]] = []
        for v in include_status_q:
            query_items.append(("include_status", v))
        for v in include_category_q:
            query_items.append(("include_category", v))
        if not query_items:
            return path
        joiner = "&" if "?" in path else "?"
        return f"{path}{joiner}{urlencode(query_items)}"

    base_run_path = f"{_path_for(request, 'dashboard_results_run', job_id=job_id, run_id=run_id)}?task_type={task_type}"
    run_data = {
        "run_id": run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "pages_visited": run.pages_visited,
        "pages_failed": run.pages_failed,
        "links_discovered": run.links_discovered,
        "task_type": task_type,
        "link_check_run_id": selected_link_check_run_id,
        "based_on_crawl_run_id": selected_crawl_run_id,
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
            "include_category": include_category,
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
            for row in ignored_links
        ],
        links={
            "main_dashboard": _path_for(request, "dashboard_schedule"),
            "job": _path_for(request, "dashboard_results_job_history", job_id=job_id),
            "run_json": with_filters(
                f"{_path_for(request, 'api_results_run_detail', job_id=job_id, run_id=run_id)}?task_type={task_type}",
                include_status_q=include_status,
                include_category_q=include_category,
            ),
            "jobs_index": _path_for(request, "dashboard_results_job_history", job_id=job_id),
            "refresh": with_filters(
                base_run_path,
                include_status_q=include_status,
                include_category_q=include_category,
            ),
            **_branding_links(request),
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
            Mount("/static", app=StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static"),
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
                "/dashboard/results/{job_id}/history",
                dashboard_results_job_history,
                methods=["GET"],
                name="dashboard_results_job_history",
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
