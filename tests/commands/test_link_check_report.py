from __future__ import annotations

import json
from datetime import date

from typer.testing import CliRunner

from app.cli.main import app
from app.config.loader import load_effective_job_config
from app.link_check.http_client import HttpCheckResult
from app.notifications.models import NotificationDispatchResult
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.job_paths import JobPaths


def test_link_check_run_writes_json_report_when_enabled(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    db_path = tmp_path / "report.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.add_page_result(run_id, "https://cardano.org/docs", 0, 200, True)
    repo.add_link(run_id, "https://cardano.org/docs", "https://ok.example", False, anchor_text="ok link text")
    repo.add_link(run_id, "https://cardano.org/docs", "https://bad.example", False, anchor_text="bad link text")
    repo.finish_run(run_id, pages_visited=1, pages_failed=0, links_discovered=2)
    connection.close()

    def fake_load_effective_job_config(job: str):  # noqa: ANN001
        cfg = load_effective_job_config(job)
        cfg["link_check"]["write_json_report"] = True
        cfg["link_check"]["implementation"] = "http"
        cfg["link_check"]["retry_count"] = 0
        cfg["link_check"]["enabled"] = True
        cfg["meta"]["job_id"] = "cardano.org"
        return cfg

    class FakeHttpLinkChecker:
        def __init__(
            self,
            timeout_seconds: int,
            follow_redirects: bool,
            *,
            artifacts_dir=None,  # noqa: ANN001
            save_failure_screenshot: bool = False,
            user_agent: str | None = None,  # noqa: ARG002
            **_kwargs: object,
        ) -> None:
            self._timeout_seconds = timeout_seconds
            self._follow_redirects = follow_redirects
            self._artifacts_dir = artifacts_dir
            self._save_failure_screenshot = save_failure_screenshot

        def check(self, url: str) -> HttpCheckResult:
            if "bad.example" in url:
                return HttpCheckResult(
                    status_code=404,
                    ok=False,
                    error_message="HTTP Error 404",
                    screenshot_file="linkcheck-failure-bad-example.png",
                )
            return HttpCheckResult(status_code=200, ok=True, error_message=None)

    custom_paths = JobPaths(
        job_root=tmp_path / "jobs" / "cardano.org",
        db_dir=tmp_path / "jobs" / "cardano.org" / "db",
        db_path=tmp_path / "jobs" / "cardano.org" / "db" / "cardano.org.sqlite3",
        logs_dir=tmp_path / "jobs" / "cardano.org" / "logs",
        log_path=tmp_path / "jobs" / "cardano.org" / "logs" / f"{date.today().isoformat()}.log",
        artifacts_dir=tmp_path / "jobs" / "cardano.org" / "artifacts",
        reports_dir=tmp_path / "jobs" / "cardano.org" / "reports",
    )
    monkeypatch.setattr("app.commands.link_check.load_effective_job_config", fake_load_effective_job_config)
    monkeypatch.setattr("app.commands.link_check.HttpLinkChecker", FakeHttpLinkChecker)
    monkeypatch.setattr("app.commands.link_check.build_job_paths", lambda job_id: custom_paths)

    result = runner.invoke(
        app,
        [
            "check",
            "run",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--hide-live-failures",
        ],
    )
    assert result.exit_code == 0
    assert "check failed:" not in result.output
    report_files = sorted(custom_paths.reports_dir.glob("report_cardano.org_*.json"))
    assert len(report_files) == 1
    payload = json.loads(report_files[0].read_text(encoding="utf-8"))
    assert payload["job"]["meta"]["crawl_run_id"] == run_id
    assert payload["job"]["summary"]["checked"] == 2
    assert payload["job"]["summary"]["categories"]["client"] == 1
    assert payload["job"]["summary"]["reportable_failures"] == 1
    assert payload["job"]["summary"]["ignored"] == 0
    assert payload["job"]["summary"]["pending_tolerance"] == 0
    assert payload["errors"]["client"][0]["target_url"] == "https://bad.example"
    assert payload["errors"]["client"][0]["blinks"][0]["anchor_text"] == "bad link text"
    assert payload["errors"]["client"][0]["screenshot_id"] == "linkcheck-failure-bad-example.png"
    assert payload["job"]["suppressed"]["ignored"] == []
    assert payload["job"]["suppressed"]["pending_tolerance"] == []


def test_link_check_run_prints_source_pages_for_live_failures(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    db_path = tmp_path / "live_failures.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.add_page_result(run_id, "https://cardano.org/docs", 0, 200, True)
    repo.add_page_result(run_id, "https://cardano.org/tutorial", 0, 200, True)
    repo.add_link(run_id, "https://cardano.org/docs", "https://bad.example", False, anchor_text="docs text")
    repo.add_link(run_id, "https://cardano.org/tutorial", "https://bad.example", False, anchor_text="tutorial text")
    repo.finish_run(run_id, pages_visited=2, pages_failed=0, links_discovered=2)
    connection.close()

    def fake_load_effective_job_config(job: str):  # noqa: ANN001
        cfg = load_effective_job_config(job)
        cfg["link_check"]["write_json_report"] = False
        cfg["link_check"]["implementation"] = "http"
        cfg["link_check"]["retry_count"] = 0
        cfg["link_check"]["enabled"] = True
        cfg["meta"]["job_id"] = "cardano.org"
        return cfg

    class FakeHttpLinkChecker:
        def __init__(
            self,
            timeout_seconds: int,
            follow_redirects: bool,
            *,
            artifacts_dir=None,  # noqa: ANN001
            save_failure_screenshot: bool = False,
            user_agent: str | None = None,  # noqa: ARG002
            **_kwargs: object,
        ) -> None:
            self._timeout_seconds = timeout_seconds
            self._follow_redirects = follow_redirects
            self._artifacts_dir = artifacts_dir
            self._save_failure_screenshot = save_failure_screenshot

        def check(self, url: str) -> HttpCheckResult:
            return HttpCheckResult(status_code=404, ok=False, error_message="HTTP Error 404")

    custom_paths = JobPaths(
        job_root=tmp_path / "jobs" / "cardano.org",
        db_dir=tmp_path / "jobs" / "cardano.org" / "db",
        db_path=tmp_path / "jobs" / "cardano.org" / "db" / "cardano.org.sqlite3",
        logs_dir=tmp_path / "jobs" / "cardano.org" / "logs",
        log_path=tmp_path / "jobs" / "cardano.org" / "logs" / f"{date.today().isoformat()}.log",
        artifacts_dir=tmp_path / "jobs" / "cardano.org" / "artifacts",
        reports_dir=tmp_path / "jobs" / "cardano.org" / "reports",
    )
    monkeypatch.setattr("app.commands.link_check.load_effective_job_config", fake_load_effective_job_config)
    monkeypatch.setattr("app.commands.link_check.HttpLinkChecker", FakeHttpLinkChecker)
    monkeypatch.setattr("app.commands.link_check.build_job_paths", lambda job_id: custom_paths)

    result = runner.invoke(
        app,
        [
            "check",
            "run",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--hide-progress",
            "--show-live-failures",
        ],
    )
    assert result.exit_code == 0
    assert "check failed: https://bad.example -> HTTP Error 404" in result.output
    assert "└ on https://cardano.org/docs" in result.output
    assert "└ on https://cardano.org/tutorial" in result.output
    assert "↳ text: docs text" in result.output
    assert "↳ text: tutorial text" in result.output


def test_link_check_run_max_blinks_reports_new_across_runs(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    db_path = tmp_path / "max-blinks.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run1 = repo.create_run("cardano.org")
    repo.add_page_result(run1, "https://cardano.org/docs", 0, 200, True)
    repo.add_link(run1, "https://cardano.org/docs", "https://a-bad.example", False, anchor_text="A")
    repo.add_link(run1, "https://cardano.org/docs", "https://b-bad.example", False, anchor_text="B")
    repo.finish_run(run1, pages_visited=1, pages_failed=0, links_discovered=2)
    run2 = repo.create_run("cardano.org")
    repo.add_page_result(run2, "https://cardano.org/docs", 0, 200, True)
    repo.add_link(run2, "https://cardano.org/docs", "https://a-bad.example", False, anchor_text="A")
    repo.add_link(run2, "https://cardano.org/docs", "https://b-bad.example", False, anchor_text="B")
    repo.finish_run(run2, pages_visited=1, pages_failed=0, links_discovered=2)
    connection.close()

    def fake_load_effective_job_config(job: str):  # noqa: ANN001
        cfg = load_effective_job_config(job)
        cfg["link_check"]["write_json_report"] = False
        cfg["link_check"]["implementation"] = "http"
        cfg["link_check"]["retry_count"] = 0
        cfg["link_check"]["enabled"] = True
        cfg["meta"]["job_id"] = "cardano.org"
        cfg["notifications"]["enabled"] = True
        cfg["notifications"]["max_blinks_per_run"] = 1
        return cfg

    class FakeHttpLinkChecker:
        def __init__(
            self,
            timeout_seconds: int,
            follow_redirects: bool,
            *,
            artifacts_dir=None,  # noqa: ANN001
            save_failure_screenshot: bool = False,
            user_agent: str | None = None,  # noqa: ARG002
            **_kwargs: object,
        ) -> None:
            self._timeout_seconds = timeout_seconds
            self._follow_redirects = follow_redirects
            self._artifacts_dir = artifacts_dir
            self._save_failure_screenshot = save_failure_screenshot

        def check(self, url: str) -> HttpCheckResult:
            return HttpCheckResult(status_code=404, ok=False, error_message="HTTP Error 404")

    sent_targets: list[str] = []

    class FakeService:
        def send_broken_link(self, notifications, payload):  # noqa: ANN001
            _ = notifications
            sent_targets.append(payload.target_url)
            return [
                NotificationDispatchResult(
                    destination_id="slack-primary",
                    provider="slack",
                    success=True,
                    message_ref=None,
                    error=None,
                )
            ]

    monkeypatch.setattr("app.commands.link_check.load_effective_job_config", fake_load_effective_job_config)
    monkeypatch.setattr("app.commands.link_check.HttpLinkChecker", FakeHttpLinkChecker)
    monkeypatch.setattr("app.commands.link_check.NotificationService", lambda: FakeService())

    first = runner.invoke(
        app,
        [
            "check",
            "run",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(run1),
            "--max-blinks",
            "1",
            "--hide-live-failures",
        ],
    )
    assert first.exit_code == 0
    assert sent_targets == ["https://a-bad.example"]

    second = runner.invoke(
        app,
        [
            "check",
            "run",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(run2),
            "--max-blinks",
            "1",
            "--hide-live-failures",
        ],
    )
    assert second.exit_code == 0
    assert sent_targets == ["https://a-bad.example", "https://b-bad.example"]

