from __future__ import annotations

from app.notifications.models import BrokenLinkNotification, InboundActionEvent, NotificationDispatchResult
from app.notifications.service import NotificationService


class _FakeAdapter:
    def __init__(self) -> None:
        self.sent = 0

    def send_broken_link(self, destination, notification):  # noqa: ANN001
        self.sent += 1
        return NotificationDispatchResult(
            destination_id=destination["id"],
            provider=destination["type"],
            success=True,
            message_ref=f"{destination['id']}-ref",
        )

    def parse_inbound_action(self, destination, raw_event):  # noqa: ANN001
        _ = destination
        _ = raw_event
        return InboundActionEvent(
            provider="slack",
            destination_id="slack-primary",
            action="on_hold",
            target_url="https://broken.example",
            actor_id="U123",
            actor_display="Tester",
            channel_id="C123",
            message_ref="171111.222",
            note="forwarded to maintainers",
            until="2026-04-30T10:00:00+00:00",
        )


def test_notification_service_send_fanout(monkeypatch) -> None:
    fake = _FakeAdapter()

    def _adapter(_destination_type: str):  # noqa: ANN001
        return fake

    monkeypatch.setattr("app.notifications.service.get_adapter", _adapter)
    service = NotificationService()
    results = service.send_broken_link(
        {
            "enabled": True,
            "destinations": [
                {
                    "type": "slack",
                    "id": "one",
                    "enabled": True,
                    "channel_id": "C1",
                    "webhook_env": "W1",
                    "bot_token_env": "B1",
                    "action_aliases": {
                        "ignore": "x",
                        "claim": "eyes",
                        "on_hold": "pause_button",
                        "resolve": "white_check_mark",
                        "retest": "curly_loop",
                    },
                    "reminders": {"enabled": True, "days_after_first_alert": [3]},
                },
                {
                    "type": "slack",
                    "id": "two",
                    "enabled": False,
                    "channel_id": "C2",
                    "webhook_env": "W2",
                    "bot_token_env": "B2",
                    "action_aliases": {
                        "ignore": "x",
                        "claim": "eyes",
                        "on_hold": "pause_button",
                        "resolve": "white_check_mark",
                        "retest": "curly_loop",
                    },
                    "reminders": {"enabled": True, "days_after_first_alert": [3]},
                },
            ],
        },
        BrokenLinkNotification(
            job_id="job-1",
            run_id=7,
            target_url="https://broken.example",
            checked_at="2026-04-16T10:00:00+00:00",
            status_code=404,
            error_message="HTTP Error 404",
            decision_state="reportable",
            source_refs=[],
        ),
    )
    assert len(results) == 1
    assert results[0].destination_id == "one"
    assert fake.sent == 1


def test_notification_service_parse_inbound_validates(monkeypatch) -> None:
    fake = _FakeAdapter()

    def _adapter(_destination_type: str):  # noqa: ANN001
        return fake

    monkeypatch.setattr("app.notifications.service.get_adapter", _adapter)
    service = NotificationService()
    parsed = service.parse_inbound_action(
        {
            "type": "slack",
            "id": "slack-primary",
            "enabled": True,
            "channel_id": "C1",
            "webhook_env": "W1",
            "bot_token_env": "B1",
            "action_aliases": {
                "ignore": "x",
                "claim": "eyes",
                "on_hold": "pause_button",
                "resolve": "white_check_mark",
                "retest": "curly_loop",
            },
            "reminders": {"enabled": True, "days_after_first_alert": [3]},
        },
        {"type": "message", "text": "ignored"},
    )
    assert parsed.error is None
    assert parsed.event is not None
    assert parsed.event.action == "on_hold"
