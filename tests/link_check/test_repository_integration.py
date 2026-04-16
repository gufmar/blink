from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


def test_link_check_repository_read_write(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    run_id = repo.create_run("job-1")
    repo.add_page_result(run_id, "https://example.org", 0, 200, True)
    repo.add_link(
        run_id=run_id,
        source_url="https://example.org",
        target_url="https://a.example",
        is_internal=False,
        anchor_text="primary anchor",
    )
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
    source_refs = repo.list_source_page_refs_for_targets(run_id, ["https://a.example"])
    assert source_refs["https://a.example"][0].anchor_text == "primary anchor"

    latest_a = [row for row in latest if row.target_url == "https://a.example"][0]
    shot_id = repo.add_link_check_screenshot(
        link_check_result_id=latest_a.row_id,
        crawl_run_id=run_id,
        target_url=latest_a.target_url,
        status_code=latest_a.status_code,
        error_message=latest_a.error_message,
        artifact_file="linkcheck-failure-a.png",
    )
    assert shot_id > 0
    mapped = repo.list_latest_screenshots_by_result_ids([latest_a.row_id])
    assert mapped[latest_a.row_id].artifact_file == "linkcheck-failure-a.png"

    connection.close()


def test_link_ignore_rules_and_failure_state(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "ignore.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    expires_at = (datetime.now(tz=UTC) + timedelta(days=5)).isoformat()
    exact_id = repo.add_link_ignore_rule(
        job_id="job-1",
        match_type="exact",
        pattern="https://a.example/path",
        reason="manual exact",
        expires_at=expires_at,
        created_by="tester",
        source="cli",
    )
    contains_id = repo.add_link_ignore_rule(
        job_id="job-1",
        match_type="contains",
        pattern="trackme.example",
        reason="manual contains",
    )

    rules = repo.list_link_ignore_rules(job_id="job-1")
    assert len(rules) == 2
    assert {row.rule_id for row in rules} == {exact_id, contains_id}

    match_exact = repo.find_matching_link_ignore_rule(
        job_id="job-1",
        target_url="https://a.example/path",
    )
    assert match_exact is not None
    assert match_exact.rule_id == exact_id
    match_contains = repo.find_matching_link_ignore_rule(
        job_id="job-1",
        target_url="https://sub.trackme.example/a",
    )
    assert match_contains is not None
    assert match_contains.rule_id == contains_id

    deactivated = repo.deactivate_link_ignore_rule(job_id="job-1", rule_id=contains_id)
    assert deactivated is True
    match_after = repo.find_matching_link_ignore_rule(
        job_id="job-1",
        target_url="https://sub.trackme.example/a",
    )
    assert match_after is None

    state_1 = repo.record_link_failure_state(
        job_id="job-1",
        target_url="https://a.example/path",
        error_category="server",
        status_code=503,
        error_message="HTTP Error 503",
    )
    assert state_1.consecutive_failures == 1
    state_2 = repo.record_link_failure_state(
        job_id="job-1",
        target_url="https://a.example/path",
        error_category="server",
        status_code=503,
        error_message="HTTP Error 503",
    )
    assert state_2.consecutive_failures == 2
    fetched = repo.get_link_failure_state(
        job_id="job-1",
        target_url="https://a.example/path",
        error_category="server",
    )
    assert fetched is not None
    assert fetched.consecutive_failures == 2
    cleared = repo.clear_link_failure_state(job_id="job-1", target_url="https://a.example/path")
    assert cleared == 1
    assert (
        repo.get_link_failure_state(
            job_id="job-1",
            target_url="https://a.example/path",
            error_category="server",
        )
        is None
    )
    connection.close()


def test_list_ignore_rule_impacts_matches_latest_run_links(tmp_path) -> None:
    connection = connect_sqlite(tmp_path / "impact.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)

    run1 = repo.create_run("job-1")
    repo.add_page_result(run1, "https://site.example/p1", 0, 200, True)
    repo.add_link(run1, "https://site.example/p1", "https://only-run1.example", False, anchor_text="old link")
    repo.finish_run(run1, 1, 0, 1)

    run2 = repo.create_run("job-1")
    repo.add_page_result(run2, "https://site.example/p2", 0, 200, True)
    repo.add_link(run2, "https://site.example/p2", "https://a.example/path", False, anchor_text="exact link")
    repo.add_link(run2, "https://site.example/p2", "https://sub.group.example/a", False, anchor_text="group A")
    repo.add_link(run2, "https://site.example/p2", "https://sub.group.example/b", False, anchor_text="group B")
    repo.add_link(run2, "https://site.example/p2", "https://expired.example/path", False, anchor_text="expired")
    repo.finish_run(run2, 1, 0, 4)

    exact_id = repo.add_link_ignore_rule(
        job_id="job-1",
        match_type="exact",
        pattern="https://a.example/path",
        reason="exact",
    )
    contains_id = repo.add_link_ignore_rule(
        job_id="job-1",
        match_type="contains",
        pattern="group.example",
        reason="contains",
    )
    _inactive_id = repo.add_link_ignore_rule(
        job_id="job-1",
        match_type="contains",
        pattern="only-run1.example",
        reason="inactive",
    )
    repo.deactivate_link_ignore_rule(job_id="job-1", rule_id=_inactive_id)
    repo.add_link_ignore_rule(
        job_id="job-1",
        match_type="exact",
        pattern="https://expired.example/path",
        reason="expired",
        expires_at=(datetime.now(tz=UTC) - timedelta(days=1)).isoformat(),
    )

    impacts = repo.list_ignore_rule_impacts(job_id="job-1", run_id=run2, active_only=True)
    assert impacts
    assert {row.rule_id for row in impacts} == {exact_id, contains_id}
    assert {row.target_url for row in impacts} == {
        "https://a.example/path",
        "https://sub.group.example/a",
        "https://sub.group.example/b",
    }
    assert all((row.source_page_url or "").startswith("https://site.example/") for row in impacts)

    only_contains = repo.list_ignore_rule_impacts(job_id="job-1", run_id=run2, rule_id=contains_id)
    assert only_contains
    assert all(row.rule_id == contains_id for row in only_contains)
    assert {row.target_url for row in only_contains} == {
        "https://sub.group.example/a",
        "https://sub.group.example/b",
    }
    connection.close()
