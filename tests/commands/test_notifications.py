from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def test_notifications_test_command_sends_greeting(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    db_path = tmp_path / "notify.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.finish_run(run_id, pages_visited=4, pages_failed=1, links_discovered=9)
    connection.close()

    class FakeService:
        def send_message(self, notifications, message):  # noqa: ANN001
            assert notifications["enabled"] is True
            assert "job_id=cardano.org" in message.body
            return [
                type(
                    "Dispatch",
                    (),
                    {"success": True, "provider": "slack", "destination_id": "slack-primary", "error": None},
                )()
            ]

    monkeypatch.setattr("app.commands.notifications.NotificationService", lambda: FakeService())
    result = runner.invoke(
        app,
        [
            "notifications",
            "test",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
        ],
    )
    assert result.exit_code == 0
    assert "Test notification sent via slack:slack-primary" in result.output


def test_crawl_run_sends_summary_notification_when_enabled(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    db_path = tmp_path / "crawl-summary.db"
    sent_titles: list[str] = []

    class FakeService:
        def send_message(self, notifications, message):  # noqa: ANN001
            assert notifications["crawl_summary_on_run"] is True
            sent_titles.append(message.title)
            return [
                type(
                    "Dispatch",
                    (),
                    {"success": True, "provider": "slack", "destination_id": "slack-primary", "error": None},
                )()
            ]

    class FakeRenderer:
        def __init__(
            self,
            navigation_timeout_seconds: int,
            network_idle_seconds: int,
            playwright_wait_seconds: int,
            browser_settings,
            observability,
            artifacts_dir=None,
        ) -> None:
            _ = navigation_timeout_seconds
            _ = network_idle_seconds
            _ = playwright_wait_seconds
            _ = browser_settings
            _ = observability
            _ = artifacts_dir

        def open_session(self) -> None:
            return None

        def close_session(self) -> None:
            return None

        def render(self, url: str):  # noqa: ANN001
            from app.render.playwright_client import RenderResult

            return RenderResult(
                requested_url=url,
                url=url,
                status_code=200,
                html="<html><body></body></html>",
                response_headers={},
                challenge_detected=False,
                request_failures=[],
                console_messages=[],
                screenshot_path=None,
                html_snapshot_path=None,
            )

    monkeypatch.setattr("app.commands.notifications.NotificationService", lambda: FakeService())
    monkeypatch.setattr("app.commands.crawl.NotificationService", lambda: FakeService())
    monkeypatch.setattr("app.commands.crawl.PlaywrightRenderer", FakeRenderer)
    result = runner.invoke(
        app,
        [
            "crawl",
            "run",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--max-pages",
            "1",
        ],
    )
    assert result.exit_code == 0
    assert sent_titles
    assert sent_titles[0].startswith("Blink crawl summary")
