from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def test_link_check_show_lists_latest_results_for_run(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "show.db"
    connection = connect_sqlite(db_path)
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    run_id = repo.create_run("cardano.org")
    repo.add_page_result(run_id, "https://cardano.org/docs", 0, 200, True)
    repo.add_link(run_id, "https://cardano.org/docs", "https://ok.example", False)
    repo.add_link(run_id, "https://cardano.org/docs", "https://bad.example", False, anchor_text="broken ext link")
    repo.finish_run(run_id, pages_visited=1, pages_failed=0, links_discovered=2)

    links = repo.list_links_for_check(run_id)
    by_url = {item.target_url: item for item in links}
    repo.add_link_check_result(
        crawl_link_id=by_url["https://ok.example"].link_id,
        crawl_run_id=run_id,
        link_check_run_id=None,
        target_url="https://ok.example",
        status_code=200,
        ok=True,
        error_message=None,
    )
    repo.add_link_check_result(
        crawl_link_id=by_url["https://bad.example"].link_id,
        crawl_run_id=run_id,
        link_check_run_id=None,
        target_url="https://bad.example",
        status_code=404,
        ok=False,
        error_message="HTTP Error 404",
    )
    connection.close()

    result = runner.invoke(
        app,
        [
            "link-check",
            "show",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--run-id",
            str(run_id),
            "--only-failed",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    assert "bad.example" in result.output
    assert "HTTP Error 404" in result.output
    assert "cardano.org/docs" in result.output
    assert "broken ext link" in result.output
    assert "decision_state" in result.output
    assert "ok.example" not in result.output

