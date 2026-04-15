from __future__ import annotations

import json
from datetime import date

from typer.testing import CliRunner

from app.cli.main import app
from app.config.loader import load_effective_job_config
from app.link_check.http_client import HttpCheckResult
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
    repo.add_link(run_id, "https://cardano.org/docs", "https://ok.example", False)
    repo.add_link(run_id, "https://cardano.org/docs", "https://bad.example", False)
    repo.finish_run(run_id, pages_visited=1, pages_failed=0, links_discovered=2)
    connection.close()

    def fake_load_effective_job_config(job: str):  # noqa: ANN001
        cfg = load_effective_job_config(job)
        cfg["link_check"]["write_json_report"] = True
        cfg["link_check"]["retry_count"] = 0
        cfg["link_check"]["enabled"] = True
        cfg["meta"]["job_id"] = "cardano.org"
        return cfg

    class FakeHttpLinkChecker:
        def __init__(self, timeout_seconds: int, follow_redirects: bool) -> None:
            self._timeout_seconds = timeout_seconds
            self._follow_redirects = follow_redirects

        def check(self, url: str) -> HttpCheckResult:
            if "bad.example" in url:
                return HttpCheckResult(status_code=404, ok=False, error_message="HTTP Error 404")
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
            "link-check",
            "run",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
        ],
    )
    assert result.exit_code == 0
    report_files = sorted(custom_paths.reports_dir.glob("report_cardano.org_*.json"))
    assert len(report_files) == 1
    payload = json.loads(report_files[0].read_text(encoding="utf-8"))
    assert payload["meta"]["crawl_run_id"] == run_id
    assert payload["summary"]["checked"] == 2
    assert payload["summary"]["categories"]["client"] == 1
    assert payload["errors"]["client"][0]["target_url"] == "https://bad.example"
    assert "https://cardano.org/docs" in payload["errors"]["client"][0]["source_pages"]

