"""Validation rules for lifecycle actions from notification channels."""

from __future__ import annotations

from dataclasses import dataclass

from app.notifications.models import InboundActionEvent

ALLOWED_ACTIONS = {"claim", "ignore", "on_hold", "resolve", "retest"}


@dataclass(frozen=True)
class LifecycleActionValidationResult:
    ok: bool
    error: str | None = None


def validate_inbound_action(event: InboundActionEvent) -> LifecycleActionValidationResult:
    """Validate channel action payload before persistence/state transition."""
    if event.action not in ALLOWED_ACTIONS:
        return LifecycleActionValidationResult(ok=False, error=f"unsupported_action:{event.action}")
    if not event.actor_id.strip():
        return LifecycleActionValidationResult(ok=False, error="actor_id_required")
    if not event.channel_id.strip():
        return LifecycleActionValidationResult(ok=False, error="channel_id_required")
    if not event.message_ref.strip():
        return LifecycleActionValidationResult(ok=False, error="message_ref_required")

    from_reaction = event.source == "reaction"
    if not event.target_url.strip():
        if from_reaction and event.action in {"claim", "ignore", "on_hold", "resolve", "retest"}:
            pass
        else:
            return LifecycleActionValidationResult(ok=False, error="target_url_required")

    if event.action == "on_hold" and not from_reaction:
        if event.until is None or not event.until.strip():
            return LifecycleActionValidationResult(ok=False, error="on_hold_until_required")
    if event.action == "ignore" and not from_reaction:
        if event.until is None or not str(event.until).strip():
            return LifecycleActionValidationResult(ok=False, error="ignore_until_required")
    return LifecycleActionValidationResult(ok=True)

