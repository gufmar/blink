"""Resolve Slack inbound actions against DB state and apply lifecycle transitions."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.job_config import JobConfig, SlackNotificationDestinationConfig
from app.notifications.models import InboundActionEvent
from app.notifications.slack.policy import merged_slack_lifecycle_policy
from app.persistence.repository import CrawlRepository

_DAYS = re.compile(r"^(\d+)d$", re.IGNORECASE)


def _resolve_destination(config: JobConfig, destination_id: str) -> SlackNotificationDestinationConfig | None:
    for d in config["notifications"]["destinations"]:
        if d["type"] == "slack" and d["id"] == destination_id:
            return d
    return None


def resolve_inbound_action_target_url(
    repository: CrawlRepository,
    *,
    job_id: str,
    event: InboundActionEvent,
) -> InboundActionEvent | None:
    if event.target_url.strip():
        return event
    alert = repository.get_open_link_alert_by_slack_message(
        job_id=job_id,
        slack_channel_id=event.channel_id.strip(),
        message_ts=event.message_ref.strip(),
    )
    if alert is None:
        return None
    return replace(event, target_url=alert.target_url)


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _compute_hold_until(
    *,
    policy: dict[str, Any],
    until: str | None,
    from_reaction: bool,
    now: datetime,
) -> str | None:
    default_days = int(policy["on_hold_default_days"])
    max_days = int(policy["on_hold_max_days"])
    if from_reaction or until is None:
        days = _clamp_int(default_days, 1, max_days)
    elif until.lower() == "infinite":
        days = max_days
    else:
        m = _DAYS.match(until.strip())
        if m:
            days = _clamp_int(int(m.group(1)), 1, max_days)
        else:
            return until.strip()
    return (now + timedelta(days=days)).isoformat()


def _compute_ignore_until(
    *,
    policy: dict[str, Any],
    until: str | None,
    from_reaction: bool,
    now: datetime,
) -> tuple[str | None, bool]:
    """Returns (ignore_until_iso or None for infinite, ok)."""
    allow_inf = bool(policy["ignore_allow_infinite"])
    default_days = int(policy["ignore_default_days"])
    if from_reaction:
        return (now + timedelta(days=max(default_days, 1))).isoformat(), True
    if until is None:
        return None, False
    tok = until.strip().lower()
    if tok == "infinite":
        if not allow_inf:
            return None, False
        return None, True
    m = _DAYS.match(until.strip())
    if m:
        days = max(int(m.group(1)), 1)
        return (now + timedelta(days=days)).isoformat(), True
    return until.strip(), True


def apply_inbound_slack_lifecycle(
    *,
    config: JobConfig,
    repository: CrawlRepository,
    job_id: str,
    event: InboundActionEvent,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Resolve target, update alert state, optionally enqueue retest. Returns (ok, error_code)."""
    now = now or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    destination = _resolve_destination(config, event.destination_id)
    if destination is None:
        return False, "unknown_destination"

    policy = merged_slack_lifecycle_policy(destination)

    resolved = resolve_inbound_action_target_url(repository, job_id=job_id, event=event)
    if resolved is None:
        return False, "unresolved_target"

    alert = repository.get_open_link_alert_by_target(job_id=job_id, target_url=resolved.target_url.strip())
    if alert is None:
        return False, "unknown_alert"

    from_reaction = resolved.source == "reaction"

    if resolved.action == "retest":
        thread_ts = alert.slack_thread_ts or alert.slack_root_ts
        if thread_ts is None or alert.slack_channel_id is None:
            return False, "missing_slack_thread"
        repository.enqueue_link_retest(
            job_id=job_id,
            alert_id=alert.alert_id,
            target_url=alert.target_url,
            slack_destination_id=alert.slack_destination_id or destination["id"],
            slack_channel_id=alert.slack_channel_id,
            slack_thread_ts=thread_ts,
            requested_by=resolved.actor_id or None,
        )
        repository.append_link_alert_event(
            alert_id=alert.alert_id,
            event_type="retest_enqueued",
            actor_id=resolved.actor_id or None,
            payload={"target_url": alert.target_url},
        )
        return True, None

    if not policy["enabled"]:
        return False, "lifecycle_disabled"

    if resolved.action == "claim":
        repository.update_link_alert_lifecycle_fields(
            alert_id=alert.alert_id,
            human_bucket="claimed",
            owner_actor_id=resolved.actor_id,
        )
        repository.append_link_alert_event(
            alert_id=alert.alert_id,
            event_type="claimed",
            actor_id=resolved.actor_id,
            payload=None,
        )
        return True, None

    if resolved.action == "resolve":
        ok = repository.resolve_open_link_alert_by_id(alert_id=alert.alert_id, resolved_at=now.isoformat())
        if not ok:
            return False, "resolve_failed"
        repository.append_link_alert_event(
            alert_id=alert.alert_id,
            event_type="resolved",
            actor_id=resolved.actor_id,
            payload=None,
        )
        return True, None

    if resolved.action == "on_hold":
        hold = _compute_hold_until(policy=policy, until=resolved.until, from_reaction=from_reaction, now=now)
        if hold is None:
            return False, "invalid_hold"
        repository.update_link_alert_lifecycle_fields(
            alert_id=alert.alert_id,
            human_bucket="on_hold",
            hold_until=hold,
            clear_ignore=True,
        )
        repository.append_link_alert_event(
            alert_id=alert.alert_id,
            event_type="on_hold",
            actor_id=resolved.actor_id,
            payload={"hold_until": hold},
        )
        return True, None

    if resolved.action == "ignore":
        ignore_until, ok = _compute_ignore_until(
            policy=policy, until=resolved.until, from_reaction=from_reaction, now=now
        )
        if not ok:
            return False, "invalid_ignore"
        repository.update_link_alert_lifecycle_fields(
            alert_id=alert.alert_id,
            human_bucket="ignored",
            ignore_until=ignore_until,
            clear_hold=True,
            clear_owner=True,
        )
        repository.append_link_alert_event(
            alert_id=alert.alert_id,
            event_type="ignored",
            actor_id=resolved.actor_id,
            payload={"ignore_until": ignore_until},
        )
        return True, None

    return False, "unsupported_action"
