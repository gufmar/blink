from __future__ import annotations

from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def test_schema_init_and_history_pruning(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    run_id = repo.create_run("job-1")
    for idx in range(3):
        repo.add_page_result(
            run_id=run_id,
            url="https://example.org/page",
            depth=idx,
            status_code=200,
            ok=True,
            html=f"<html>{idx}</html>",
            main_text=f"text-{idx}",
        )
    repo.prune_page_history("https://example.org/page", keep=2)

    kept = connection.execute(
        "SELECT COUNT(*) FROM crawl_pages WHERE url = ?",
        ("https://example.org/page",),
    ).fetchone()[0]
    assert kept == 2

    repo.finish_run(run_id, pages_visited=3, pages_failed=0, links_discovered=0)
    row = connection.execute("SELECT finished_at, pages_visited FROM crawl_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["finished_at"] is not None
    assert row["pages_visited"] == 3
    connection.close()


def test_query_helpers_for_runs_pages_and_links(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("job-2")
    repo.add_page_result(run_id, "https://example.org/docs/xyz/", 1, 200, True)
    repo.add_page_result(run_id, "https://example.org/fail", 1, None, False, error_message="timeout")
    repo.add_link(run_id, "https://example.org", "https://external.a", False)
    repo.add_link(run_id, "https://example.org", "https://external.a", False)
    repo.add_link(run_id, "https://example.org", "https://external.b", False)
    repo.finish_run(run_id, pages_visited=2, pages_failed=1, links_discovered=3)

    history = repo.list_run_history("job-2", limit=5)
    assert len(history) == 1
    assert history[0].run_id == run_id

    pages = repo.list_crawled_pages(run_id, search="docs/xyz", limit=10)
    assert len(pages) == 1
    assert pages[0].url.endswith("docs/xyz/")

    failed_pages = repo.list_crawled_pages(run_id, only_failed=True)
    assert len(failed_pages) == 1
    assert failed_pages[0].ok is False

    links = repo.list_external_links(run_id, limit=10)
    assert len(links) == 2
    assert links[0].seen_count >= links[1].seen_count

    counts = repo.get_table_counts()
    assert counts["crawl_runs"] == 1
    assert counts["crawl_pages"] == 2
    assert counts["crawl_links"] == 3
    assert counts["run_pages"] == 2
    assert counts["run_external_links"] == 2
    connection.close()


def test_run_page_external_links_and_source_query(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("job-prov")
    repo.add_page_result(run_id, "https://example.org/p1", 0, 200, True, main_text="alpha")
    repo.add_page_result(run_id, "https://example.org/p2", 0, 200, True, main_text="beta")
    ext = "https://ext.example/out"
    repo.add_link(run_id, "https://example.org/p1", ext, False)
    repo.add_link(run_id, "https://example.org/p2", ext, False)
    repo.finish_run(run_id, 2, 0, 2)

    n = connection.execute("SELECT COUNT(*) FROM run_page_external_links WHERE run_id = ?", (run_id,)).fetchone()[0]
    assert n == 2
    sources = repo.list_source_pages_for_external(run_id, target_url=ext)
    assert {s.source_page_url for s in sources} == {"https://example.org/p1", "https://example.org/p2"}
    connection.close()


def test_compute_page_text_metrics_between_runs(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    r1 = repo.create_run("j1")
    repo.add_page_result(r1, "https://example.org/a", 0, 200, True, main_text="hello world")
    repo.finish_run(r1, 1, 0, 0)
    repo.compute_run_diffs("j1", r1)
    repo.compute_page_text_metrics(
        "j1",
        r1,
        significant_change_threshold_percent=25.0,
        text_compare_max_chars=10_000,
    )

    r2 = repo.create_run("j1")
    repo.add_page_result(r2, "https://example.org/a", 0, 200, True, main_text="hello universe")
    repo.finish_run(r2, 1, 0, 0)
    repo.compute_run_diffs("j1", r2)
    repo.compute_page_text_metrics(
        "j1",
        r2,
        significant_change_threshold_percent=25.0,
        text_compare_max_chars=10_000,
    )

    row = connection.execute(
        """
        SELECT text_similarity_prev, text_change_percent_prev, text_compared_to_run_id, text_significant_change
        FROM run_pages WHERE run_id = ? AND page_id = (SELECT id FROM pages WHERE url = ?)
        """,
        (r2, "https://example.org/a"),
    ).fetchone()
    assert row["text_compared_to_run_id"] == r1
    assert row["text_similarity_prev"] is not None
    assert float(row["text_change_percent_prev"]) > 0
    assert row["text_significant_change"] in (0, 1)
    metrics = repo.list_page_content_metrics(r2, only_significant=False, limit=10)
    assert len(metrics) == 1
    assert metrics[0].url == "https://example.org/a"
    connection.close()


def test_compute_run_diffs_for_pages_and_external_links(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    run1 = repo.create_run("job-3")
    repo.add_page_result(run1, "https://example.org/a", 0, 200, True)
    repo.add_page_result(run1, "https://example.org/b", 0, 200, True)
    repo.add_link(run1, "https://example.org/a", "https://ext-1.example", False)
    repo.add_link(run1, "https://example.org/b", "https://ext-2.example", False)
    repo.finish_run(run1, pages_visited=2, pages_failed=0, links_discovered=2)
    repo.compute_run_diffs(job_id="job-3", run_id=run1)

    run2 = repo.create_run("job-3")
    repo.add_page_result(run2, "https://example.org/b", 0, 200, True)
    repo.add_page_result(run2, "https://example.org/c", 0, 200, True)
    repo.add_link(run2, "https://example.org/b", "https://ext-2.example", False)
    repo.add_link(run2, "https://example.org/c", "https://ext-3.example", False)
    repo.finish_run(run2, pages_visited=2, pages_failed=0, links_discovered=2)
    repo.compute_run_diffs(job_id="job-3", run_id=run2)

    appeared_pages = repo.list_page_diffs(run_id=run2, appeared=True)
    disappeared_pages = repo.list_page_diffs(run_id=run2, appeared=False)
    assert {row.url for row in appeared_pages} == {"https://example.org/c"}
    assert {row.url for row in disappeared_pages} == {"https://example.org/a"}

    appeared_links = repo.list_external_link_diffs(run_id=run2, appeared=True)
    disappeared_links = repo.list_external_link_diffs(run_id=run2, appeared=False)
    assert {row.target_url for row in appeared_links} == {"https://ext-3.example"}
    assert {row.target_url for row in disappeared_links} == {"https://ext-1.example"}

    connection.close()
