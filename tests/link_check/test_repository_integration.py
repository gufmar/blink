from __future__ import annotations

from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def test_link_check_repository_read_write(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    run_id = repo.create_run("job-1")
    repo.add_page_result(run_id, "https://example.org", 0, 200, True)
    repo.add_link(run_id=run_id, source_url="https://example.org", target_url="https://a.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://example.org", target_url="https://a.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://example.org", target_url="https://b.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://example.org", target_url="https://example.org/page", is_internal=True)

    latest = repo.get_latest_run_id("job-1")
    assert latest == run_id

    links = repo.list_links_for_check(crawl_run_id=run_id)
    assert len(links) == 2
    assert [link.target_url for link in links] == ["https://a.example", "https://b.example"]

    first = links[0]
    repo.add_link_check_result(
        crawl_link_id=first.link_id,
        crawl_run_id=first.crawl_run_id,
        target_url=first.target_url,
        status_code=200,
        ok=True,
        error_message=None,
    )

    stored = connection.execute(
        "SELECT target_url, status_code, ok FROM link_check_results ORDER BY id ASC"
    ).fetchall()
    assert len(stored) == 1
    assert stored[0]["target_url"] == "https://a.example"
    assert stored[0]["status_code"] == 200
    assert stored[0]["ok"] == 1

    # Add newer check for same URL and ensure latest-per-target query works.
    repo.add_link_check_result(
        crawl_link_id=first.link_id,
        crawl_run_id=first.crawl_run_id,
        target_url=first.target_url,
        status_code=503,
        ok=False,
        error_message="HTTP Error 503",
    )
    second = links[1]
    repo.add_link_check_result(
        crawl_link_id=second.link_id,
        crawl_run_id=second.crawl_run_id,
        target_url=second.target_url,
        status_code=404,
        ok=False,
        error_message="HTTP Error 404",
    )

    latest = repo.list_latest_link_check_results(run_id)
    assert len(latest) == 2
    status_by_url = {row.target_url: row.status_code for row in latest}
    assert status_by_url["https://a.example"] == 503
    assert status_by_url["https://b.example"] == 404

    source_pages = repo.list_source_pages_for_targets(run_id, ["https://a.example", "https://b.example"])
    assert set(source_pages["https://a.example"]) == {"https://example.org"}
    assert set(source_pages["https://b.example"]) == {"https://example.org"}

    connection.close()
