"""Adapter protocol for notification providers."""

from __future__ import annotations

from typing import Any, Protocol

from app.models.job_config import SlackNotificationDestinationConfig
from app.notifications.models import (
    BrokenLinkNotification,
    InboundActionEvent,
    NotificationDispatchResult,
    NotificationMessage,
)


class NotificationAdapter(Protocol):
    provider: str

    def send_broken_link(
        self,
        destination: SlackNotificationDestinationConfig,
        notification: BrokenLinkNotification,
    ) -> NotificationDispatchResult:
        """Send one broken-link notification."""

    def send_message(
        self,
        destination: SlackNotificationDestinationConfig,
        message: NotificationMessage,
    ) -> NotificationDispatchResult:
        """Send one generic message (greeting/test/summary)."""

    def parse_inbound_action(
        self,
        destination: SlackNotificationDestinationConfig,
        raw_event: dict[str, Any],
    ) -> InboundActionEvent | None:
        """Parse inbound provider webhook event into a normalized action."""

