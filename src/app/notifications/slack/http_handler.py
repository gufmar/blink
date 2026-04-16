"""Shared Slack Events API envelope handling for CLI and HTTP server."""

from __future__ import annotations

import os
from typing import Any

from app.models.job_config import JobConfig, SlackNotificationDestinationConfig
from app.notifications.service import NotificationService
from app.notifications.slack.lifecycle_actions import apply_inbound_slack_lifecycle
from app.persistence.repository import CrawlRepository


def slack_event_channel_hint(payload: dict[str, Any]) -> str:
    """Best-effort channel id for matching a Slack destination (envelope or inner event)."""
    ev = payload.get("event")
    if not isinstance(ev, dict):
        ev = payload
    if str(ev.get("type") or "") == "reaction_added":
        item = ev.get("item") or {}
        return str(item.get("channel") or ev.get("channel") or "")
    if str(ev.get("type") or "") == "message":
        return str(ev.get("channel") or "")
    return ""


def notifications_signing_secret_env_name(config: JobConfig) -> str:
    return str(config["notifications"].get("slack_signing_secret_env") or "BLINK_SLACK_SIGNING_SECRET")


def resolve_notifications_signing_secret(config: JobConfig) -> str | None:
    """Return signing secret from env, or None if unset."""
    name = notifications_signing_secret_env_name(config)
    raw = os.getenv(name)
    return raw.strip() if raw else None


def apply_inbound_slack_from_envelope(
    config: JobConfig,
    repository: CrawlRepository,
    raw_envelope: dict[str, Any],
) -> tuple[bool, str | None]:
    """
    Parse and apply one lifecycle action from a Slack Events API envelope or inner event dict.

    Returns (True, None) on success, (False, error_code) on failure or no-op that CLI treats as error.
    """
    service = NotificationService()
    channel_hint = slack_event_channel_hint(raw_envelope)
    matched: SlackNotificationDestinationConfig | None = None
    parsed = None
    for destination in config["notifications"]["destinations"]:
        if destination["type"] != "slack" or not destination["enabled"]:
            continue
        if channel_hint and destination.get("channel_id") and channel_hint != destination["channel_id"]:
            continue
        result = service.parse_inbound_action(destination, raw_envelope)
        if result.event is not None:
            matched = destination
            parsed = result.event
            break
    if parsed is None or matched is None:
        return False, "unparsed"
    ok, err = apply_inbound_slack_lifecycle(
        config=config,
        repository=repository,
        job_id=config["meta"]["job_id"],
        event=parsed,
    )
    if not ok:
        return False, err or "apply_failed"
    return True, None
