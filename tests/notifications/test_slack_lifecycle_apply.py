from __future__ import annotations

from datetime import UTC, datetime

from app.models.job_config import JobConfig
from app.notifications.models import InboundActionEvent
from app.notifications.slack.lifecycle_actions import apply_inbound_slack_lifecycle
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def _minimal_job() -> JobConfig:
    return {  # type: ignore[return-value]
        "meta": {"job_id": "job-lx"},
        "notifications": {
            "enabled": True,
            "max_blinks_per_run": 1,
            "crawl_summary_on_run": False,
            "destinations": [
                {
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
                    "reminders": {"enabled": False, "days_after_first_alert": [1]},
                    "lifecycle": {
                        "enabled": True,
                        "on_hold_default_days": 3,
                        "on_hold_max_days": 30,
                        "ignore_default_days": 5,
                        "ignore_allow_infinite": True,
                    },
                }
            ],
        },
    }


def test_apply_retest_enqueues(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "c.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("job-lx")
    alert = repo.upsert_open_link_alert(
        job_id="job-lx",
        target_url="https://x.example",
        run_id=run_id,
        checked_at="2026-04-16T10:00:00+00:00",
        status_code=404,
        error_message="nope",
    )
    repo.update_link_alert_slack_refs(
        alert_id=alert.alert_id,
        slack_destination_id="slack-primary",
        slack_channel_id="C123",
        slack_root_ts="1.1",
        slack_thread_ts="1.1",
        slack_bootstrap_ts="1.2",
    )
    ev = InboundActionEvent(
        provider="slack",
        destination_id="slack-primary",
        action="retest",
        target_url="",
        actor_id="U1",
        actor_display=None,
        channel_id="C123",
        message_ref="1.1",
        source="reaction",
    )
    ok, err = apply_inbound_slack_lifecycle(
        config=_minimal_job(),
        repository=repo,
        job_id="job-lx",
        event=ev,
        now=datetime(2026, 4, 16, tzinfo=UTC),
    )
    assert ok and err is None
    pending = repo.list_pending_link_retests(job_id="job-lx", limit=5)
    assert len(pending) == 1
    connection.close()


def test_apply_on_hold_from_reaction_sets_hold_until(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "c.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("job-lx")
    alert = repo.upsert_open_link_alert(
        job_id="job-lx",
        target_url="https://y.example",
        run_id=run_id,
        checked_at="2026-04-16T10:00:00+00:00",
        status_code=500,
        error_message="err",
    )
    repo.update_link_alert_slack_refs(
        alert_id=alert.alert_id,
        slack_destination_id="slack-primary",
        slack_channel_id="C123",
        slack_root_ts="9.9",
        slack_thread_ts="9.9",
    )
    ev = InboundActionEvent(
        provider="slack",
        destination_id="slack-primary",
        action="on_hold",
        target_url="",
        actor_id="U1",
        actor_display=None,
        channel_id="C123",
        message_ref="9.9",
        source="reaction",
    )
    now = datetime(2026, 4, 16, tzinfo=UTC)
    ok, err = apply_inbound_slack_lifecycle(config=_minimal_job(), repository=repo, job_id="job-lx", event=ev, now=now)
    assert ok and err is None
    row = repo.get_open_link_alert_by_target(job_id="job-lx", target_url="https://y.example")
    assert row is not None
    assert row.human_bucket == "on_hold"
    assert row.hold_until is not None
    connection.close()
