"""Starlette ASGI application for Slack Events API."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.config.loader import load_effective_job_config, project_root
from app.config.schema import validate_job_config
from app.models.job_config import JobConfig
from app.notifications.slack.http_handler import apply_inbound_slack_from_envelope, resolve_notifications_signing_secret
from app.notifications.slack.signature import verify_slack_signing_secret
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.job_paths import build_job_paths

_SLUG_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _json_error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code}, status_code=status)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


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


def build_app(*, jobs_root: Path | None = None) -> Starlette:
    root = (jobs_root if jobs_root is not None else project_root() / "jobs").resolve()
    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/notifications/slack/health", health, methods=["GET"]),
            Route("/notifications/slack/job/{job_slug}", slack_job_event, methods=["POST"]),
        ],
    )
    app.state.jobs_root = root
    return app
