"""Slack destination lifecycle policy defaults and merge helpers."""

from __future__ import annotations

from typing import Any

from app.models.job_config import SlackNotificationDestinationConfig


def merged_slack_lifecycle_policy(destination: SlackNotificationDestinationConfig) -> dict[str, Any]:
    """Return effective lifecycle policy dict (always includes all keys)."""
    defaults: dict[str, Any] = {
        "enabled": False,
        "post_alerts_via_bot": True,
        "on_hold_default_days": 7,
        "on_hold_max_days": 90,
        "ignore_default_days": 30,
        "ignore_allow_infinite": True,
    }
    raw = destination.get("lifecycle")
    if not isinstance(raw, dict):
        return defaults
    merged = {**defaults, **raw}
    merged["enabled"] = bool(raw.get("enabled", False))
    merged["post_alerts_via_bot"] = bool(raw.get("post_alerts_via_bot", defaults["post_alerts_via_bot"]))
    merged["on_hold_default_days"] = int(merged.get("on_hold_default_days", defaults["on_hold_default_days"]))
    merged["on_hold_max_days"] = int(merged.get("on_hold_max_days", defaults["on_hold_max_days"]))
    merged["ignore_default_days"] = int(merged.get("ignore_default_days", defaults["ignore_default_days"]))
    merged["ignore_allow_infinite"] = bool(merged.get("ignore_allow_infinite", defaults["ignore_allow_infinite"]))
    return merged
