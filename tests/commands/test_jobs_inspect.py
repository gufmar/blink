from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def test_jobs_history_pages_links_and_stats_json(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "inspect.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.add_page_result(run_id, "https://cardano.org/docs/xyz/", 1, 200, True)
    repo.add_page_result(run_id, "https://cardano.org/error", 1, None, False, error_message="boom")
    repo.add_link(run_id, "https://cardano.org/docs/xyz/", "https://external.one", False)
    repo.add_link(run_id, "https://cardano.org/docs/xyz/", "https://external.two", False)
    repo.finish_run(run_id, pages_visited=2, pages_failed=1, links_discovered=2)
    connection.close()

    result_history = runner.invoke(
        app,
        ["jobs", "history", "--job", "jobs/cardano.org.job.json", "--db", str(db_path), "--format", "json"],
    )
    assert result_history.exit_code == 0
    assert '"run_id":' in result_history.output

    result_pages = runner.invoke(
        app,
        [
            "jobs",
            "pages",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--search",
            "docs/xyz",
            "--format",
            "json",
        ],
    )
    assert result_pages.exit_code == 0
    assert "docs/xyz" in result_pages.output

    result_links = runner.invoke(
        app,
        [
            "jobs",
            "external-links",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--format",
            "json",
        ],
    )
    assert result_links.exit_code == 0
    assert "external.one" in result_links.output

    result_stats = runner.invoke(
        app,
        ["jobs", "db-stats", "--job", "jobs/cardano.org.job.json", "--db", str(db_path), "--format", "json"],
    )
    assert result_stats.exit_code == 0
    assert "table_row_counts" in result_stats.output
    assert "external_urls_distinct" in result_stats.output
    assert "db_file_size_human" in result_stats.output

    # second run to make diffs meaningful
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run2 = repo.create_run("cardano.org")
    repo.add_page_result(run2, "https://cardano.org/docs/xyz/", 1, 200, True)
    repo.add_page_result(run2, "https://cardano.org/new", 1, 200, True)
    repo.add_link(run2, "https://cardano.org/new", "https://external.three", False)
    repo.finish_run(run2, pages_visited=2, pages_failed=0, links_discovered=1)
    repo.compute_run_diffs(job_id="cardano.org", run_id=run2)
    connection.close()

    result_pages_diff = runner.invoke(
        app,
        [
            "jobs",
            "pages-diff",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(run2),
            "--change",
            "all",
            "--format",
            "json",
        ],
    )
    assert result_pages_diff.exit_code == 0
    assert "appeared" in result_pages_diff.output
    assert "disappeared" in result_pages_diff.output

    result_links_diff = runner.invoke(
        app,
        [
            "jobs",
            "external-links-diff",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(run2),
            "--change",
            "all",
            "--format",
            "json",
        ],
    )
    assert result_links_diff.exit_code == 0
    assert "target_url" in result_links_diff.output

    result_sources = runner.invoke(
        app,
        [
            "jobs",
            "external-link-sources",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(run2),
            "--target-url",
            "https://external.three",
            "--format",
            "json",
        ],
    )
    assert result_sources.exit_code == 0
    assert "cardano.org/new" in result_sources.output


def test_jobs_pages_content_metrics_json(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "metrics.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    r1 = repo.create_run("cardano.org")
    repo.add_page_result(r1, "https://cardano.org/page-a", 0, 200, True, main_text="alpha beta gamma")
    repo.finish_run(r1, 1, 0, 0)
    repo.compute_run_diffs("cardano.org", r1)
    repo.compute_page_text_metrics(
        "cardano.org",
        r1,
        significant_change_threshold_percent=25.0,
        text_compare_max_chars=10_000,
    )
    r2 = repo.create_run("cardano.org")
    repo.add_page_result(r2, "https://cardano.org/page-a", 0, 200, True, main_text="alpha beta delta")
    repo.finish_run(r2, 1, 0, 0)
    repo.compute_run_diffs("cardano.org", r2)
    repo.compute_page_text_metrics(
        "cardano.org",
        r2,
        significant_change_threshold_percent=25.0,
        text_compare_max_chars=10_000,
    )
    connection.close()

    result = runner.invoke(
        app,
        [
            "jobs",
            "pages-content-metrics",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(r2),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    assert "text_change_percent_prev" in result.output
    assert "page-a" in result.output
