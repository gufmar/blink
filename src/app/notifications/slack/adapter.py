"""Slack outbound/inbound adapter implementation."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib import request
from urllib.error import URLError

from app.models.job_config import SlackNotificationDestinationConfig
from app.notifications.models import BrokenLinkNotification, NotificationDispatchResult, NotificationMessage
from app.notifications.slack.parser import parse_slack_inbound_action
from app.notifications.slack.policy import merged_slack_lifecycle_policy
from app.notifications.slack.web_api import chat_post_message


def _build_slack_text(notification: BrokenLinkNotification) -> str:
    def _pretty_timestamp(value: str | None) -> str:
        raw = value or notification.checked_at
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed = parsed.astimezone(UTC)
        return parsed.strftime("%d.%m.%Y"), parsed.strftime("%H:%M")

    status = str(notification.status_code) if notification.status_code is not None else "error"
    reason = notification.error_message or "unknown error"
    detected_date, detected_time = _pretty_timestamp(notification.first_detected_at)
    lines = [
        f":warning: I found a new broken link {notification.target_url}",
    ]
    for ref in notification.source_refs:
        if ref.anchor_text:
            lines.append(f"on {ref.page_url} (text: {ref.anchor_text})")
        else:
            lines.append(f"on {ref.page_url}")
    lines.extend(
        [
            "",
            f"status: {status}",
            f"reason: {reason}",
            (
                f"This error was first detected on {detected_date} at {detected_time} and is still present, "
                "so I thought a manual review and decision is necessary."
            ),
        ]
    )
    return "\n".join(lines)


def _emoji(alias: str) -> str:
    return f":{alias}:"


def _bootstrap_prompt(destination: SlackNotificationDestinationConfig) -> str:
    aliases = destination["action_aliases"]
    lines = [
        "React on the alert message above, or reply in this thread with commands.",
        f"- Claim: {_emoji(aliases['claim'])}",
        f"- On hold (default duration): {_emoji(aliases['on_hold'])}",
        f"- Ignore (default duration): {_emoji(aliases['ignore'])}",
        f"- Immediate retest: {_emoji(aliases['retest'])}",
        f"- Resolve: {_emoji(aliases['resolve'])}",
        "",
        "Overrides (reply in this thread):",
        "`on_hold <url> 14d waiting on vendor`",
        "`ignore <url> infinite not our content`",
        "`ignore <url> 30d`",
        "`retest <url>`",
    ]
    return "\n".join(lines)


class SlackNotificationAdapter:
    provider = "slack"

    def _post_text(self, destination: SlackNotificationDestinationConfig, text: str) -> NotificationDispatchResult:
        webhook_url = os.getenv(destination["webhook_env"])
        if not webhook_url:
            return NotificationDispatchResult(
                destination_id=destination["id"],
                provider=self.provider,
                success=False,
                error=f"missing_env:{destination['webhook_env']}",
            )
        payload = {
            "channel": destination["channel_id"],
            "text": text,
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10):
                return NotificationDispatchResult(
                    destination_id=destination["id"],
                    provider=self.provider,
                    success=True,
                )
        except URLError as exc:
            return NotificationDispatchResult(
                destination_id=destination["id"],
                provider=self.provider,
                success=False,
                error=str(exc),
            )

    def send_broken_link(
        self,
        destination: SlackNotificationDestinationConfig,
        notification: BrokenLinkNotification,
    ) -> NotificationDispatchResult:
        text = _build_slack_text(notification)
        policy = merged_slack_lifecycle_policy(destination)
        channel = str(destination.get("channel_id") or "").strip()
        use_bot = bool(policy["enabled"] and policy.get("post_alerts_via_bot", True))
        if use_bot:
            token = os.getenv(destination["bot_token_env"])
            if not token:
                return NotificationDispatchResult(
                    destination_id=destination["id"],
                    provider=self.provider,
                    success=False,
                    error=f"missing_env:{destination['bot_token_env']}",
                )
            if not channel:
                return NotificationDispatchResult(
                    destination_id=destination["id"],
                    provider=self.provider,
                    success=False,
                    error="missing_channel_id",
                )
            ok, root_ts, err = chat_post_message(token=token, channel=channel, text=text)
            if not ok or not root_ts:
                return NotificationDispatchResult(
                    destination_id=destination["id"],
                    provider=self.provider,
                    success=False,
                    error=err or "chat.postMessage_failed",
                )
            boot = _bootstrap_prompt(destination)
            ok_b, boot_ts, boot_err = chat_post_message(token=token, channel=channel, text=boot, thread_ts=root_ts)
            return NotificationDispatchResult(
                destination_id=destination["id"],
                provider=self.provider,
                success=ok,
                message_ref=root_ts,
                error=None if ok_b else (boot_err or "bootstrap_failed"),
                slack_channel_id=channel,
                slack_root_ts=root_ts,
                slack_bootstrap_ts=boot_ts if ok_b else None,
            )
        return self._post_text(destination, text)

    def send_message(
        self,
        destination: SlackNotificationDestinationConfig,
        message: NotificationMessage,
    ) -> NotificationDispatchResult:
        text = f"{message.title}\n{message.body}"
        return self._post_text(destination, text)

    def parse_inbound_action(
        self,
        destination: SlackNotificationDestinationConfig,
        raw_event: dict[str, Any],
    ):
        return parse_slack_inbound_action(destination, raw_event)
