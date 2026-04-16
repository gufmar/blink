from __future__ import annotations

from app.notifications.lifecycle import validate_inbound_action
from app.notifications.models import InboundActionEvent


def test_validate_inbound_action_requires_on_hold_until_and_note() -> None:
    invalid = InboundActionEvent(
        provider="slack",
        destination_id="slack-primary",
        action="on_hold",
        target_url="https://broken.example",
        actor_id="U111",
        actor_display=None,
        channel_id="C111",
        message_ref="171111.222",
        note=None,
        until=None,
    )
    result = validate_inbound_action(invalid)
    assert result.ok is False
    assert result.error == "on_hold_until_required"

    valid = InboundActionEvent(
        provider="slack",
        destination_id="slack-primary",
        action="on_hold",
        target_url="https://broken.example",
        actor_id="U111",
        actor_display=None,
        channel_id="C111",
        message_ref="171111.222",
        note="awaiting vendor response",
        until="2026-05-01T10:00:00+00:00",
    )
    assert validate_inbound_action(valid).ok is True


def test_validate_inbound_action_on_hold_from_reaction_allows_missing_until() -> None:
    ev = InboundActionEvent(
        provider="slack",
        destination_id="slack-primary",
        action="on_hold",
        target_url="https://broken.example",
        actor_id="U111",
        actor_display=None,
        channel_id="C111",
        message_ref="171111.222",
        note=None,
        until=None,
        source="reaction",
    )
    assert validate_inbound_action(ev).ok is True


def test_validate_inbound_action_retest_requires_refs_not_target() -> None:
    ev = InboundActionEvent(
        provider="slack",
        destination_id="slack-primary",
        action="retest",
        target_url="",
        actor_id="U111",
        actor_display=None,
        channel_id="C111",
        message_ref="171111.222",
        source="reaction",
    )
    assert validate_inbound_action(ev).ok is True
