"""Process queued single-link retests and post results back to Slack threads."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from app.link_check.runner import LinkChecker
from app.models.job_config import JobConfig, SlackNotificationDestinationConfig
from app.notifications.slack.web_api import chat_post_message
from app.persistence.repository import CrawlRepository


def _destination_for_retest(
    config: JobConfig,
    *,
    destination_id: str | None,
    slack_channel_id: str,
) -> SlackNotificationDestinationConfig | None:
    if destination_id:
        for d in config["notifications"]["destinations"]:
            if d["type"] == "slack" and d["id"] == destination_id and d["enabled"]:
                return d
    for d in config["notifications"]["destinations"]:
        if d["type"] == "slack" and d["enabled"] and d.get("channel_id") == slack_channel_id:
            return d
    for d in config["notifications"]["destinations"]:
        if d["type"] == "slack" and d["enabled"]:
            return d
    return None


def process_pending_link_retests(
    *,
    config: JobConfig,
    repository: CrawlRepository,
    checker: LinkChecker,
    limit: int = 10,
    now: datetime | None = None,
) -> int:
    """Run pending retests for this job, posting thread replies when a bot token is available."""
    now = now or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    job_id = config["meta"]["job_id"]
    pending = repository.list_pending_link_retests(job_id=job_id, limit=limit)
    processed = 0
    for row in pending:
        result = checker.check(row.target_url)
        repository.complete_link_retest(
            retest_id=row.retest_id,
            result_ok=result.ok,
            status_code=result.status_code,
            error_message=result.error_message,
            processed_at=now.isoformat(),
        )
        if row.alert_id is not None:
            repository.append_link_alert_event(
                alert_id=row.alert_id,
                event_type="retest_completed",
                actor_id=None,
                payload={
                    "ok": result.ok,
                    "status_code": result.status_code,
                    "error_message": result.error_message,
                },
            )
        destination = _destination_for_retest(
            config,
            destination_id=row.slack_destination_id,
            slack_channel_id=row.slack_channel_id,
        )
        if destination is not None:
            token = os.getenv(destination["bot_token_env"])
            if token:
                if result.ok:
                    sc = result.status_code
                    text = f":white_check_mark: Retest OK for `{row.target_url}` (HTTP {sc})."
                else:
                    reason = result.error_message or (
                        f"HTTP {result.status_code}" if result.status_code is not None else "request failed"
                    )
                    text = f":warning: Still failing `{row.target_url}` — {reason}"
                chat_post_message(
                    token=token,
                    channel=row.slack_channel_id,
                    text=text,
                    thread_ts=row.slack_thread_ts,
                )
        processed += 1
    return processed
