from __future__ import annotations

from datetime import UTC, datetime

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


def test_list_page_external_link_counts_by_run(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("job-structure")
    repo.add_page_result(run_id, "https://example.org", 0, 200, True)
    repo.add_page_result(run_id, "https://example.org/docs", 1, 200, True)
    repo.add_page_result(run_id, "https://example.org/empty", 1, 200, True)
    repo.add_link(run_id, "https://example.org", "https://ext-a.example", False)
    repo.add_link(run_id, "https://example.org/docs", "https://ext-b.example", False)
    repo.add_link(run_id, "https://example.org/docs", "https://ext-c.example", False)
    repo.finish_run(run_id, 3, 0, 3)

    rows = repo.list_page_external_link_counts(run_id, limit=20)
    by_url = {row.url: row.external_count for row in rows}
    assert by_url["https://example.org"] == 1
    assert by_url["https://example.org/docs"] == 2
    assert by_url["https://example.org/empty"] == 0
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


def test_link_alert_slack_refs_events_and_retest_queue(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("job-lc")
    alert = repo.upsert_open_link_alert(
        job_id="job-lc",
        target_url="https://broken.example",
        run_id=run_id,
        checked_at="2026-04-16T10:00:00+00:00",
        status_code=404,
        error_message="missing",
    )
    repo.update_link_alert_slack_refs(
        alert_id=alert.alert_id,
        slack_destination_id="slack-primary",
        slack_channel_id="C123",
        slack_root_ts="111.222",
        slack_thread_ts="111.222",
        slack_bootstrap_ts="111.333",
    )
    found = repo.get_open_link_alert_by_slack_message(
        job_id="job-lc",
        slack_channel_id="C123",
        message_ts="111.222",
    )
    assert found is not None
    assert found.slack_root_ts == "111.222"
    repo.append_link_alert_event(alert_id=alert.alert_id, event_type="unit", actor_id="U1", payload={"k": 1})
    rid = repo.enqueue_link_retest(
        job_id="job-lc",
        alert_id=alert.alert_id,
        target_url="https://broken.example",
        slack_destination_id="slack-primary",
        slack_channel_id="C123",
        slack_thread_ts="111.222",
        requested_by="U9",
    )
    pending = repo.list_pending_link_retests(job_id="job-lc", limit=5)
    assert len(pending) == 1
    assert pending[0].retest_id == rid
    repo.complete_link_retest(
        retest_id=rid,
        result_ok=True,
        status_code=200,
        error_message=None,
        processed_at="2026-04-16T11:00:00+00:00",
    )
    pending2 = repo.list_pending_link_retests(job_id="job-lc", limit=5)
    assert pending2 == []
    connection.close()


def _seed_purge_fixture(connection, repo, job_id: str) -> dict:
    """Seed three crawl runs each with a finished link-check run, pages, links, and screenshots."""
    info: dict[str, list] = {"crawl_run_ids": [], "lc_run_ids": []}
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
        repo.add_link_check_screenshot(
            link_check_result_id=result_id,
            crawl_run_id=run_id,
            link_check_run_id=lc_run_id,
            target_url=f"https://ext-{n}.example",
            status_code=200,
            error_message=None,
            artifact_file=f"shot-{n}.png",
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
    return info


def test_purge_helpers_select_only_targeted_crawl_runs(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "purge.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    info = _seed_purge_fixture(connection, repo, "job-purge")

    middle_run = info["crawl_run_ids"][1]
    single = repo.list_crawl_runs_for_purge(job_id="job-purge", run_id=middle_run, and_older=False)
    assert [rec.run_id for rec in single] == [middle_run]
    older = repo.list_crawl_runs_for_purge(job_id="job-purge", run_id=middle_run, and_older=True)
    assert [rec.run_id for rec in older] == info["crawl_run_ids"][:2]

    counts = repo.get_purge_preview_counts_crawl(info["crawl_run_ids"][:2])
    assert counts["crawl_runs"] == 2
    assert counts["crawl_pages"] == 2
    assert counts["crawl_links"] == 2
    assert counts["link_check_runs"] == 2
    assert counts["link_check_results"] == 2
    assert counts["link_check_screenshots"] == 2

    artifacts = repo.list_artifact_files_for_crawl_runs(info["crawl_run_ids"][:2])
    assert sorted(artifacts) == ["shot-0.png", "shot-1.png"]
    connection.close()


def test_purge_link_alerts_run_ids_are_nulled_and_alerts_survive(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "purge_alerts.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    info = _seed_purge_fixture(connection, repo, "job-alerts")

    repo.upsert_open_link_alert(
        job_id="job-alerts",
        target_url="https://broken.example",
        run_id=info["crawl_run_ids"][0],
        checked_at=datetime.now(tz=UTC).isoformat(),
        status_code=500,
        error_message="500",
    )
    rule_id = repo.add_link_ignore_rule(
        job_id="job-alerts",
        match_type="contains",
        pattern="example",
        reason="testing",
        expires_at=None,
        created_by="test",
        source="test",
    )

    to_null = repo.count_link_alerts_referencing_runs(
        job_id="job-alerts", run_ids=[info["crawl_run_ids"][0]]
    )
    assert to_null == 1

    nulled = repo.null_link_alert_last_run_for_runs(
        job_id="job-alerts", run_ids=[info["crawl_run_ids"][0]]
    )
    assert nulled == 1

    repo.delete_crawl_runs([info["crawl_run_ids"][0]])

    alerts = repo.list_open_link_alerts(job_id="job-alerts")
    assert len(alerts) == 1
    assert alerts[0].last_reported_run_id is None

    rules = repo.list_link_ignore_rules(job_id="job-alerts", active_only=True, search=None, limit=10)
    assert any(rule.rule_id == rule_id for rule in rules)

    remaining = repo.list_run_history("job-alerts", limit=10)
    assert {rec.run_id for rec in remaining} == set(info["crawl_run_ids"][1:])
    connection.close()


def test_purge_link_check_run_only_cascades_link_check_data(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "purge_lc.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    info = _seed_purge_fixture(connection, repo, "job-lc-purge")

    lc_run_id = info["lc_run_ids"][0]
    crawl_run_id = info["crawl_run_ids"][0]

    counts = repo.get_purge_preview_counts_link_check([lc_run_id])
    assert counts["link_check_runs"] == 1
    assert counts["link_check_results"] == 1
    assert counts["link_check_screenshots"] == 1
    artifacts = repo.list_artifact_files_for_link_check_runs([lc_run_id])
    assert artifacts == ["shot-0.png"]

    repo.delete_link_check_runs([lc_run_id])

    crawl_runs = repo.list_run_history("job-lc-purge", limit=10)
    assert crawl_run_id in {rec.run_id for rec in crawl_runs}
    page_count = connection.execute(
        "SELECT COUNT(*) AS n FROM crawl_pages WHERE run_id = ?", (crawl_run_id,)
    ).fetchone()
    assert int(page_count["n"]) == 1
    lc_history = repo.list_link_check_run_history("job-lc-purge", limit=10)
    assert lc_run_id not in {rec.run_id for rec in lc_history}
    result_rows = connection.execute(
        "SELECT COUNT(*) AS n FROM link_check_results WHERE link_check_run_id = ?", (lc_run_id,)
    ).fetchone()
    assert int(result_rows["n"]) == 0
    connection.close()


def test_get_latest_link_check_run_id_for_crawl(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "lc-latest.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    crawl_id = repo.create_run("job-lc-latest")
    repo.finish_run(crawl_id, 1, 0, 0)
    assert repo.get_latest_link_check_run_id_for_crawl(crawl_id) is None
    repo.create_link_check_run(
        job_id="job-lc-latest",
        based_on_crawl_run_id=crawl_id,
        started_at="2024-01-01T00:00:00+00:00",
    )
    lc2 = repo.create_link_check_run(
        job_id="job-lc-latest",
        based_on_crawl_run_id=crawl_id,
        started_at="2025-01-01T00:00:00+00:00",
    )
    assert repo.get_latest_link_check_run_id_for_crawl(crawl_id) == lc2
    connection.close()


def test_count_link_check_result_totals_for_runs(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "lc-counts.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    crawl_id = repo.create_run("job-lc-counts")
    repo.add_page_result(crawl_id, "https://example.org", 0, 200, True)
    repo.add_link(
        run_id=crawl_id,
        source_url="https://example.org",
        target_url="https://broken.example",
        is_internal=False,
    )
    repo.add_link(
        run_id=crawl_id,
        source_url="https://example.org",
        target_url="https://ignored.example",
        is_internal=False,
    )
    repo.add_link(
        run_id=crawl_id,
        source_url="https://example.org",
        target_url="https://ok.example",
        is_internal=False,
    )
    targets = {row.target_url: row for row in repo.list_links_for_check(crawl_id, limit=10)}
    lc_run_id = repo.create_link_check_run(
        job_id="job-lc-counts",
        based_on_crawl_run_id=crawl_id,
        started_at="2025-01-01T00:00:00+00:00",
    )
    repo.add_link_check_result(
        crawl_link_id=targets["https://broken.example"].link_id,
        crawl_run_id=crawl_id,
        link_check_run_id=lc_run_id,
        target_url="https://broken.example",
        status_code=404,
        ok=False,
        error_message="missing",
        error_category="client",
    )
    repo.add_link_check_result(
        crawl_link_id=targets["https://ignored.example"].link_id,
        crawl_run_id=crawl_id,
        link_check_run_id=lc_run_id,
        target_url="https://ignored.example",
        status_code=403,
        ok=False,
        error_message="blocked",
        error_category="client",
        decision_state="ignored",
        decision_reason="link_check.ignore.http_status:403",
    )
    repo.add_link_check_result(
        crawl_link_id=targets["https://ok.example"].link_id,
        crawl_run_id=crawl_id,
        link_check_run_id=lc_run_id,
        target_url="https://ok.example",
        status_code=200,
        ok=True,
        error_message=None,
    )
    repo.finish_link_check_run(
        link_check_run_id=lc_run_id,
        finished_at="2025-01-01T01:00:00+00:00",
        checked_total=99,
        passed_total=88,
        failed_total=77,
        errored_total=0,
        ignored_total=66,
        pending_tolerance_total=0,
        reportable_failures_total=1,
    )

    totals = repo.count_link_check_result_totals_for_runs([lc_run_id])
    assert totals[lc_run_id] == {
        "checked_total": 3,
        "passed_total": 1,
        "failed_total": 1,
        "ignored_total": 1,
    }
    connection.close()
