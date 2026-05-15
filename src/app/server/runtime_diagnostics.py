"""Runtime paths, scheduler state, and environment diagnostics for admin views."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.config.loader import load_effective_job_config


def mask_env_value(value: str | None) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 5:
        return value
    return f"{value[:5]}…"


def collect_env_var_names(jobs_root: Path) -> list[str]:
    names: set[str] = {"BLINK_SLACK_SIGNING_SECRET", "BLINK_SCHEDULER_MAX_CONCURRENT_TASKS"}
    for job_path in sorted(jobs_root.glob("*.job.json")):
        if job_path.name.startswith("_"):
            continue
        try:
            config = load_effective_job_config(job_path)
        except (FileNotFoundError, ValueError):
            continue
        notifications = config.get("notifications") or {}
        signing_name = notifications.get("slack_signing_secret_env")
        if isinstance(signing_name, str) and signing_name.strip():
            names.add(signing_name.strip())
        destinations = notifications.get("destinations") or []
        if not isinstance(destinations, list):
            continue
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            for key in ("webhook_env", "bot_token_env"):
                raw = destination.get(key)
                if isinstance(raw, str) and raw.strip():
                    names.add(raw.strip())
    return sorted(names)


def build_runtime_diagnostics(
    *,
    jobs_root: Path,
    route_base_path: str,
    enable_scheduler: bool,
    scheduler_payload: dict[str, Any],
    signing_secret_env_name: str,
    channel_route_count: int,
) -> dict[str, Any]:
    scheduler = dict(scheduler_payload.get("scheduler") or {})
    env_rows = [
        {"name": name, "value": mask_env_value(os.getenv(name))}
        for name in collect_env_var_names(jobs_root)
    ]
    return {
        "paths": [
            ("jobs_root", str(jobs_root.resolve())),
            ("route_base_path", route_base_path or "/"),
        ],
        "service": [
            ("scheduler thread", "Running" if scheduler_payload.get("scheduler_running") else "Stopped"),
            ("scheduler enabled at startup", "yes" if enable_scheduler else "no"),
            ("max concurrent tasks", str(scheduler.get("max_concurrent_tasks", ""))),
            ("queued tasks", str(scheduler.get("queued_tasks", ""))),
            ("slack signing secret env", signing_secret_env_name),
            ("mapped slack channels", str(channel_route_count)),
        ],
        "environment": env_rows,
    }
