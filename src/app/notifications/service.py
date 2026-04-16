"""Notification dispatch service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.job_config import NotificationsConfig, SlackNotificationDestinationConfig
from app.notifications.lifecycle import validate_inbound_action
from app.notifications.models import BrokenLinkNotification, InboundActionEvent, NotificationDispatchResult, NotificationMessage
from app.notifications.registry import get_adapter


@dataclass(frozen=True)
class ParseInboundResult:
    event: InboundActionEvent | None
    error: str | None = None


class NotificationService:
    """Fan-out notifications and normalize inbound actions."""

    def send_broken_link(
        self,
        notifications: NotificationsConfig,
        notification: BrokenLinkNotification,
    ) -> list[NotificationDispatchResult]:
        if not notifications["enabled"]:
            return []
        results: list[NotificationDispatchResult] = []
        for destination in notifications["destinations"]:
            typed_destination = destination
            if not typed_destination["enabled"]:
                continue
            adapter = get_adapter(typed_destination["type"])
            results.append(adapter.send_broken_link(typed_destination, notification))
        return results

    def send_message(
        self,
        notifications: NotificationsConfig,
        message: NotificationMessage,
    ) -> list[NotificationDispatchResult]:
        if not notifications["enabled"]:
            return []
        results: list[NotificationDispatchResult] = []
        for destination in notifications["destinations"]:
            typed_destination = destination
            if not typed_destination["enabled"]:
                continue
            adapter = get_adapter(typed_destination["type"])
            results.append(adapter.send_message(typed_destination, message))
        return results

    def parse_inbound_action(
        self,
        destination: SlackNotificationDestinationConfig,
        raw_event: dict[str, Any],
    ) -> ParseInboundResult:
        adapter = get_adapter(destination["type"])
        event = adapter.parse_inbound_action(destination, raw_event)
        if event is None:
            return ParseInboundResult(event=None, error="unparsed")
        validated = validate_inbound_action(event)
        if not validated.ok:
            return ParseInboundResult(event=None, error=validated.error)
        return ParseInboundResult(event=event, error=None)

