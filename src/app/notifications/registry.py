"""Destination adapter registry."""

from __future__ import annotations

from app.notifications.base import NotificationAdapter
from app.notifications.slack.adapter import SlackNotificationAdapter


def get_adapter(destination_type: str) -> NotificationAdapter:
    if destination_type == "slack":
        return SlackNotificationAdapter()
    raise ValueError(f"Unsupported destination type: {destination_type}")

