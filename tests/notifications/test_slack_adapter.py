from __future__ import annotations

import json

from app.notifications.lifecycle import validate_inbound_action
from app.notifications.models import BrokenLinkNotification
from app.notifications.slack.adapter import SlackNotificationAdapter
from app.notifications.slack.parser import parse_slack_inbound_action


def _destination() -> dict[str, object]:
    return {
        "type": "slack",
        "id": "slack-primary",
        "enabled": True,
        "channel_id": "C123",
        "webhook_env": "BLINK_SLACK_WEBHOOK_URL",
        "bot_token_env": "BLINK_SLACK_BOT_TOKEN",
        "action_aliases": {
            "ignore": "x",
            "claim": "eyes",
            "on_hold": "pause_button",
            "resolve": "white_check_mark",
            "retest": "curly_loop",
        },
        "reminders": {"enabled": True, "days_after_first_alert": [3, 7]},
        "capabilities": {
            "supports_threads": True,
            "supports_reactions": True,
            "supports_interactive_components": True,
        },
    }


def test_parse_slack_reaction_action() -> None:
    event = parse_slack_inbound_action(
        _destination(),  # type: ignore[arg-type]
        {
            "type": "reaction_added",
            "reaction": "x",
            "user": "U111",
            "target_url": "https://broken.example",
            "item": {"channel": "C123", "ts": "171111.222"},
        },
    )
    assert event is not None
    assert event.action == "ignore"
    assert event.target_url == "https://broken.example"
    assert event.source == "reaction"


def test_parse_slack_message_on_hold_requires_fields() -> None:
    event = parse_slack_inbound_action(
        _destination(),  # type: ignore[arg-type]
        {
            "type": "message",
            "user": "U111",
            "channel": "C123",
            "ts": "171111.222",
            "text": "on_hold https://broken.example until=2026-04-30T10:00:00+00:00 escalated to vendor",
        },
    )
    assert event is not None
    validated = validate_inbound_action(event)
    assert validated.ok is True


def test_slack_adapter_send_missing_env() -> None:
    adapter = SlackNotificationAdapter()
    result = adapter.send_broken_link(
        _destination(),  # type: ignore[arg-type]
        BrokenLinkNotification(
            job_id="job-1",
            run_id=1,
            target_url="https://broken.example",
            checked_at="2026-04-16T10:00:00+00:00",
            status_code=404,
            error_message="HTTP Error 404",
            decision_state="reportable",
            source_refs=[],
        ),
    )
    assert result.success is False
    assert result.error == "missing_env:BLINK_SLACK_WEBHOOK_URL"


def test_slack_adapter_send_success(monkeypatch) -> None:
    posted: dict[str, object] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            _ = exc_type
            _ = exc
            _ = tb
            return None

    def _fake_urlopen(_req, timeout=10):  # noqa: ANN001
        _ = timeout
        body = _req.data.decode("utf-8") if _req.data else "{}"
        posted.update(json.loads(body))
        return _Resp()

    monkeypatch.setenv("BLINK_SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/T000/B000/X")
    monkeypatch.setattr("app.notifications.slack.adapter.request.urlopen", _fake_urlopen)
    adapter = SlackNotificationAdapter()
    result = adapter.send_broken_link(
        _destination(),  # type: ignore[arg-type]
        BrokenLinkNotification(
            job_id="job-1",
            run_id=1,
            target_url="https://broken.example",
            checked_at="2026-04-16T10:00:00+00:00",
            status_code=503,
            error_message="HTTP Error 503",
            decision_state="reportable",
            source_refs=[],
            first_detected_at="2026-04-16T10:00:00+00:00",
        ),
    )
    assert result.success is True
    text = str(posted.get("text") or "")
    assert text.startswith(":warning: I found a new broken link https://broken.example")
    assert "status: 503" in text
    assert "reason: HTTP Error 503" in text
    assert "first detected on 16.04.2026 at 10:00" in text


def test_slack_adapter_lifecycle_uses_bot_for_alert_and_bootstrap(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_chat_post_message(**kwargs: object) -> tuple[bool, str | None, str | None]:
        calls.append(kwargs)
        if len(calls) == 1:
            return True, "root.ts", None
        return True, "boot.ts", None

    monkeypatch.setattr("app.notifications.slack.adapter.chat_post_message", _fake_chat_post_message)
    monkeypatch.setenv("BLINK_SLACK_BOT_TOKEN", "xoxb-fake-token")
    dest = _destination()
    dest["lifecycle"] = {"enabled": True, "post_alerts_via_bot": True}  # type: ignore[assignment]
    adapter = SlackNotificationAdapter()
    result = adapter.send_broken_link(
        dest,  # type: ignore[arg-type]
        BrokenLinkNotification(
            job_id="job-1",
            run_id=1,
            target_url="https://broken.example",
            checked_at="2026-04-16T10:00:00+00:00",
            status_code=503,
            error_message="HTTP Error 503",
            decision_state="reportable",
            source_refs=[],
            first_detected_at="2026-04-16T10:00:00+00:00",
        ),
    )
    assert result.success is True
    assert result.slack_root_ts == "root.ts"
    assert result.slack_bootstrap_ts == "boot.ts"
    assert result.slack_channel_id == "C123"
    assert len(calls) == 2
    assert calls[0].get("thread_ts") in (None, "")
    assert calls[1].get("thread_ts") == "root.ts"
