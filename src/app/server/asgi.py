"""Starlette ASGI application for Slack Events API."""

from __future__ import annotations

import html
import json
import os
import re
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

_SLUG_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _json_error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code}, status_code=status)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def api_schedule(request: Request) -> JSONResponse:
    svc: BlinkSchedulerService = request.app.state.scheduler_service
    return JSONResponse(svc.build_schedule_payload())


async def schedule_dashboard(request: Request) -> HTMLResponse:
    svc: BlinkSchedulerService = request.app.state.scheduler_service
    payload = svc.build_schedule_payload()
    dumped = json.dumps(payload, indent=2)
    safe = html.escape(dumped)
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Blink schedules</title>
</head>
<body>
  <h1>Blink schedules</h1>
  <p><a href="/health">health</a> · <a href="/api/schedule">JSON</a></p>
  <pre>{safe}</pre>
</body>
</html>
"""
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
            Route("/health", health, methods=["GET"]),
            Route("/notifications/slack/health", health, methods=["GET"]),
            Route("/api/schedule", api_schedule, methods=["GET"]),
            Route("/dashboard", schedule_dashboard, methods=["GET"]),
            Route("/notifications/slack/events", slack_events, methods=["POST"]),
            Route("/notifications/slack/job/{job_slug}", slack_job_event, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    app.state.jobs_root = root
    app.state.channel_routes = routes
    app.state.signing_secret_env_name = signing_env_name
    app.state.scheduler_service = scheduler_service
    app.state.enable_scheduler = enable_scheduler
    return app
