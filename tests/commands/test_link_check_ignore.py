from __future__ import annotations

from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from app.cli.main import app
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def test_link_check_ignore_add_list_remove(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "ignore-cli.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    connection.close()

    add_result = runner.invoke(
        app,
        [
            "link-check",
            "ignore",
            "add",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--pattern",
            "example.com",
            "--match-type",
            "contains",
            "--days",
            "7",
            "--reason",
            "temporary suppression",
        ],
    )
    assert add_result.exit_code == 0
    assert "Ignore rule added: id=" in add_result.output

    list_result = runner.invoke(
        app,
        [
            "link-check",
            "ignore",
            "list",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--format",
            "json",
        ],
    )
    assert list_result.exit_code == 0
    assert '"pattern": "example.com"' in list_result.output
    assert '"active": true' in list_result.output

    remove_result = runner.invoke(
        app,
        [
            "link-check",
            "ignore",
            "remove",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--id",
            "1",
        ],
    )
    assert remove_result.exit_code == 0
    assert "Ignore rule deactivated: id=1" in remove_result.output

    list_active = runner.invoke(
        app,
        [
            "link-check",
            "ignore",
            "list",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--active-only",
            "--format",
            "json",
        ],
    )
    assert list_active.exit_code == 0
    assert list_active.output.strip() == "[]"


def test_link_check_ignore_impact_latest_and_run_id(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "ignore-impact.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    run1 = repo.create_run("cardano.org")
    repo.add_page_result(run1, "https://cardano.org/r1", 0, 200, True)
    repo.add_link(run1, "https://cardano.org/r1", "https://legacy.example/path", False, anchor_text="legacy")
    repo.finish_run(run1, 1, 0, 1)

    run2 = repo.create_run("cardano.org")
    repo.add_page_result(run2, "https://cardano.org/r2", 0, 200, True)
    repo.add_link(run2, "https://cardano.org/r2", "https://target.example/a", False, anchor_text="A")
    repo.add_link(run2, "https://cardano.org/r2", "https://target.example/b", False, anchor_text="B")
    repo.finish_run(run2, 1, 0, 2)

    repo.add_link_ignore_rule(job_id="cardano.org", match_type="contains", pattern="target.example", reason="active")
    repo.add_link_ignore_rule(
        job_id="cardano.org",
        match_type="contains",
        pattern="legacy.example",
        reason="expired",
        expires_at=(datetime.now(tz=UTC) - timedelta(days=1)).isoformat(),
    )
    connection.close()

    latest_result = runner.invoke(
        app,
        [
            "link-check",
            "ignore",
            "impact",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--format",
            "json",
        ],
    )
    assert latest_result.exit_code == 0
    assert '"run_id": 2' in latest_result.output
    assert '"affected_external_urls": 2' in latest_result.output
    assert '"pattern": "target.example"' in latest_result.output

    run1_result = runner.invoke(
        app,
        [
            "link-check",
            "ignore",
            "impact",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(run1),
            "--format",
            "json",
        ],
    )
    assert run1_result.exit_code == 0
    assert '"rows": []' in run1_result.output
