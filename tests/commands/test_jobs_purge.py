from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from app.cli.main import app
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def _seed_purge_db(db_path, artifacts_dir, job_id: str = "cardano.org") -> dict:
    """Create three crawl runs each with a finished link-check run, link result, and screenshot."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    info: dict[str, list] = {"crawl_run_ids": [], "lc_run_ids": [], "shot_files": []}
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    for n in range(3):
        run_id = repo.create_run(job_id)
        info["crawl_run_ids"].append(run_id)
        repo.add_page_result(run_id, f"https://example.org/p{n}", 0, 200, True)
        repo.add_link(run_id, f"https://example.org/p{n}", f"https://ext-{n}.example", False)
        repo.finish_run(run_id, pages_visited=1, pages_failed=0, links_discovered=1)
        lc_run_id = repo.create_link_check_run(
            job_id=job_id,
            based_on_crawl_run_id=run_id,
            started_at=datetime.now(tz=UTC).isoformat(),
        )
        info["lc_run_ids"].append(lc_run_id)
        link_row = connection.execute(
            "SELECT id FROM crawl_links WHERE run_id = ? LIMIT 1", (run_id,)
        ).fetchone()
        result_id = repo.add_link_check_result(
            crawl_link_id=int(link_row["id"]),
            crawl_run_id=run_id,
            link_check_run_id=lc_run_id,
            target_url=f"https://ext-{n}.example",
            status_code=200,
            ok=True,
            error_message=None,
        )
        shot_name = f"shot-{n}.png"
        info["shot_files"].append(shot_name)
        (artifacts_dir / shot_name).write_bytes(b"png-data")
        repo.add_link_check_screenshot(
            link_check_result_id=result_id,
            crawl_run_id=run_id,
            link_check_run_id=lc_run_id,
            target_url=f"https://ext-{n}.example",
            status_code=200,
            error_message=None,
            artifact_file=shot_name,
        )
        repo.finish_link_check_run(
            link_check_run_id=lc_run_id,
            finished_at=datetime.now(tz=UTC).isoformat(),
            checked_total=1,
            passed_total=1,
            failed_total=0,
            errored_total=0,
            ignored_total=0,
            pending_tolerance_total=0,
            reportable_failures_total=0,
        )
    connection.close()
    return info


def test_purge_aborts_without_yes_when_user_says_no(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "purge.db"
    artifacts = tmp_path / "artifacts"
    info = _seed_purge_db(db_path, artifacts)

    result = runner.invoke(
        app,
        [
            "jobs",
            "purge",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--artifacts-dir",
            str(artifacts),
            "--task-type",
            "crawl",
            "--run-id",
            str(info["crawl_run_ids"][0]),
        ],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "Aborted" in result.output

    connection = connect_sqlite(db_path)
    repo = CrawlRepository(connection)
    history = repo.list_run_history("cardano.org", limit=10)
    assert {rec.run_id for rec in history} == set(info["crawl_run_ids"])
    connection.close()
    for shot in info["shot_files"]:
        assert (artifacts / shot).exists()


def test_purge_crawl_single_run_with_yes_deletes_artifacts_and_keeps_others(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "purge.db"
    artifacts = tmp_path / "artifacts"
    info = _seed_purge_db(db_path, artifacts)

    target = info["crawl_run_ids"][0]
    result = runner.invoke(
        app,
        [
            "jobs",
            "purge",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--artifacts-dir",
            str(artifacts),
            "--task-type",
            "crawl",
            "--run-id",
            str(target),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Purge complete" in result.output

    connection = connect_sqlite(db_path)
    repo = CrawlRepository(connection)
    remaining = {rec.run_id for rec in repo.list_run_history("cardano.org", limit=10)}
    assert target not in remaining
    assert remaining == set(info["crawl_run_ids"][1:])
    lc_remaining = {rec.run_id for rec in repo.list_link_check_run_history("cardano.org", limit=10)}
    assert info["lc_run_ids"][0] not in lc_remaining
    connection.close()

    assert not (artifacts / info["shot_files"][0]).exists()
    for shot in info["shot_files"][1:]:
        assert (artifacts / shot).exists()


def test_purge_crawl_and_older_deletes_chain_and_nulls_alert_run_ids(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "purge.db"
    artifacts = tmp_path / "artifacts"
    info = _seed_purge_db(db_path, artifacts)

    connection = connect_sqlite(db_path)
    repo = CrawlRepository(connection)
    repo.upsert_open_link_alert(
        job_id="cardano.org",
        target_url="https://broken.example",
        run_id=info["crawl_run_ids"][1],
        checked_at=datetime.now(tz=UTC).isoformat(),
        status_code=500,
        error_message="500",
    )
    rule_id = repo.add_link_ignore_rule(
        job_id="cardano.org",
        match_type="contains",
        pattern="ignored.example",
        reason="testing",
        expires_at=None,
        created_by="test",
        source="test",
    )
    connection.close()

    middle = info["crawl_run_ids"][1]
    result = runner.invoke(
        app,
        [
            "jobs",
            "purge",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--artifacts-dir",
            str(artifacts),
            "--task-type",
            "crawl",
            "--run-id",
            str(middle),
            "--and-older",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "link_alerts_nulled=1" in result.output

    connection = connect_sqlite(db_path)
    repo = CrawlRepository(connection)
    remaining_runs = {rec.run_id for rec in repo.list_run_history("cardano.org", limit=10)}
    assert remaining_runs == {info["crawl_run_ids"][2]}

    alerts = repo.list_open_link_alerts(job_id="cardano.org")
    assert len(alerts) == 1
    assert alerts[0].last_reported_run_id is None

    rules = repo.list_link_ignore_rules(job_id="cardano.org", active_only=True, search=None, limit=10)
    assert any(rule.rule_id == rule_id for rule in rules)
    connection.close()

    assert not (artifacts / info["shot_files"][0]).exists()
    assert not (artifacts / info["shot_files"][1]).exists()
    assert (artifacts / info["shot_files"][2]).exists()


def test_purge_link_check_only_does_not_delete_crawl(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "purge.db"
    artifacts = tmp_path / "artifacts"
    info = _seed_purge_db(db_path, artifacts)

    target_lc = info["lc_run_ids"][0]
    crawl_run = info["crawl_run_ids"][0]
    result = runner.invoke(
        app,
        [
            "jobs",
            "purge",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--artifacts-dir",
            str(artifacts),
            "--task-type",
            "link-check",
            "--run-id",
            str(target_lc),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Purge complete" in result.output

    connection = connect_sqlite(db_path)
    repo = CrawlRepository(connection)
    crawl_runs = {rec.run_id for rec in repo.list_run_history("cardano.org", limit=10)}
    assert crawl_run in crawl_runs
    lc_remaining = {rec.run_id for rec in repo.list_link_check_run_history("cardano.org", limit=10)}
    assert target_lc not in lc_remaining
    connection.close()
    assert not (artifacts / info["shot_files"][0]).exists()


def test_purge_unknown_run_id_errors_cleanly(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "purge.db"
    artifacts = tmp_path / "artifacts"
    _seed_purge_db(db_path, artifacts)

    result = runner.invoke(
        app,
        [
            "jobs",
            "purge",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--task-type",
            "crawl",
            "--run-id",
            "9999",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "No crawl run" in result.output


def test_purge_invalid_task_type_errors(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "purge.db"
    artifacts = tmp_path / "artifacts"
    _seed_purge_db(db_path, artifacts)

    result = runner.invoke(
        app,
        [
            "jobs",
            "purge",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--task-type",
            "bogus",
            "--run-id",
            "1",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Invalid --task-type" in result.output
