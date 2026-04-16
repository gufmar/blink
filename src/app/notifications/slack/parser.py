"""Slack inbound event parsing."""

from __future__ import annotations

import re
from typing import Any

from app.models.job_config import SlackNotificationDestinationConfig
from app.notifications.models import InboundActionEvent


def _unwrap_slack_event(raw_event: dict[str, Any]) -> dict[str, Any]:
    inner = raw_event.get("event")
    if isinstance(inner, dict):
        return inner
    return raw_event


def _map_reaction_to_action(
    reaction: str,
    destination: SlackNotificationDestinationConfig,
) -> str | None:
    aliases = destination["action_aliases"]
    for action, alias in aliases.items():
        if reaction == alias:
            return action
    return None


_DAYS_SUFFIX = re.compile(r"^(\d+)d$", re.IGNORECASE)


def parse_slack_inbound_action(
    destination: SlackNotificationDestinationConfig,
    raw_event: dict[str, Any],
) -> InboundActionEvent | None:
    """Convert Slack Events API (or test) payload to normalized action event."""
    ev = _unwrap_slack_event(raw_event)
    event_type = str(ev.get("type") or "")
    if event_type == "reaction_added":
        reaction = str(ev.get("reaction") or "")
        action = _map_reaction_to_action(reaction, destination)
        if action is None:
            return None
        item = ev.get("item") or {}
        channel_id = str(item.get("channel") or ev.get("channel") or destination["channel_id"])
        message_ref = str(item.get("ts") or "")
        if not message_ref:
            return None
        target_url = str(ev.get("target_url") or "")
        return InboundActionEvent(
            provider="slack",
            destination_id=destination["id"],
            action=action,
            target_url=target_url,
            actor_id=str(ev.get("user") or ""),
            actor_display=None,
            channel_id=channel_id,
            message_ref=message_ref,
            note=str(ev.get("note") or "") or None,
            until=str(ev.get("until") or "") or None,
            source="reaction",
        )

    if event_type == "message":
        text = str(ev.get("text") or "").strip()
        if not text:
            return None
        parts = text.split()
        if len(parts) < 2:
            return None
        action = parts[0].lower()
        if action not in {"claim", "ignore", "on_hold", "resolve", "retest"}:
            return None
        target_url = parts[1]
        rest = parts[2:]
        until: str | None = None
        note: str | None = None
        if action == "retest":
            note = " ".join(rest).strip() or None
        elif action in {"claim", "resolve"}:
            note = " ".join(rest).strip() or None
        elif action == "on_hold":
            if rest:
                m = _DAYS_SUFFIX.match(rest[0])
                if m:
                    until = f"{m.group(1)}d"
                    note = " ".join(rest[1:]).strip() or None
                else:
                    tail = list(rest)
                    for idx, item in enumerate(list(tail)):
                        if item.lower().startswith("until="):
                            until = item.split("=", 1)[1]
                            tail.pop(idx)
                            break
                    note = " ".join(tail).strip() or None
        elif action == "ignore":
            if rest:
                tok = rest[0].lower()
                if tok == "infinite":
                    until = "infinite"
                    note = " ".join(rest[1:]).strip() or None
                else:
                    m = _DAYS_SUFFIX.match(rest[0])
                    if m:
                        until = f"{m.group(1)}d"
                        note = " ".join(rest[1:]).strip() or None
                    else:
                        tail = list(rest)
                        for idx, item in enumerate(list(tail)):
                            if item.lower().startswith("until="):
                                until = item.split("=", 1)[1]
                                tail.pop(idx)
                                break
                        note = " ".join(tail).strip() or None
        return InboundActionEvent(
            provider="slack",
            destination_id=destination["id"],
            action=action,
            target_url=target_url,
            actor_id=str(ev.get("user") or ""),
            actor_display=None,
            channel_id=str(ev.get("channel") or destination["channel_id"]),
            message_ref=str(ev.get("ts") or ""),
            note=note,
            until=until,
            source="message",
        )
    return None
