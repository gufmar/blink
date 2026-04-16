"""Provider-agnostic notification models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceBlinkRef:
    page_url: str
    anchor_text: str | None = None


@dataclass(frozen=True)
class BrokenLinkNotification:
    job_id: str
    run_id: int
    target_url: str
    checked_at: str
    status_code: int | None
    error_message: str | None
    decision_state: str
    source_refs: list[SourceBlinkRef]
    first_detected_at: str | None = None


@dataclass(frozen=True)
class NotificationMessage:
    job_id: str
    title: str
    body: str


@dataclass(frozen=True)
class NotificationDispatchResult:
    destination_id: str
    provider: str
    success: bool
    message_ref: str | None = None
    error: str | None = None
    slack_channel_id: str | None = None
    slack_root_ts: str | None = None
    slack_bootstrap_ts: str | None = None


@dataclass(frozen=True)
class InboundActionEvent:
    provider: str
    destination_id: str
    action: str
    target_url: str
    actor_id: str
    actor_display: str | None
    channel_id: str
    message_ref: str
    note: str | None = None
    until: str | None = None
    """Semantic duration for overrides, e.g. ``14d``, ``infinite``, or ISO timestamp."""
    source: str = "message"
    """``reaction`` | ``message`` — affects validation defaults for emoji-driven actions."""

