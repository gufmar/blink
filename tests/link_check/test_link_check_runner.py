from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from app.config.loader import load_effective_job_config
from app.link_check.http_client import HttpCheckResult
from app.link_check.runner import run_link_check
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema


class FakeChecker:
    def __init__(self, outcomes: dict[str, list[HttpCheckResult]]) -> None:
        self._outcomes = outcomes
        self._calls = defaultdict(int)

    def check(self, url: str) -> HttpCheckResult:
        idx = self._calls[url]
        self._calls[url] += 1
        values = self._outcomes[url]
        return values[idx] if idx < len(values) else values[-1]

    def call_count(self, url: str) -> int:
        return self._calls[url]


def test_run_link_check_retries_and_persists(tmp_path: Path) -> None:
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://ok.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://bad.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://internal.example/page", is_internal=True)

    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["meta"]["job_id"] = "cardano.org"
    config["link_check"]["enabled"] = True
    config["link_check"]["retry_count"] = 1

    checker = FakeChecker(
        {
            "https://ok.example": [
                HttpCheckResult(status_code=503, ok=False, error_message="503"),
                HttpCheckResult(status_code=200, ok=True, error_message=None),
            ],
            "https://bad.example": [
                HttpCheckResult(status_code=404, ok=False, error_message="404"),
                HttpCheckResult(status_code=404, ok=False, error_message="404"),
            ],
        }
    )
    seen_results: list[tuple[str, int | None, bool]] = []
    statuses: list[str] = []

    summary = run_link_check(
        config=config,
        repository=repo,
        checker=checker,
        run_id=run_id,
        status_hook=statuses.append,
        result_hook=lambda url, result: seen_results.append((url, result.status_code, result.ok)),
    )
    assert summary.crawl_run_id == run_id
    assert summary.checked == 2
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.errored == 0
    assert summary.ignored == 0
    assert summary.pending_tolerance == 0
    assert summary.reportable_failures == 1
    assert checker.call_count("https://ok.example") == 2
    assert checker.call_count("https://bad.example") == 2
    assert statuses
    assert "Checking progress=0% (1/2): " in statuses[0]
    assert len(seen_results) == 1
    assert seen_results[0][0] == "https://bad.example"
    assert seen_results[0][2] is False

    rows = connection.execute(
        "SELECT target_url, ok, status_code FROM link_check_results ORDER BY id ASC"
    ).fetchall()
    assert len(rows) == 2
    connection.close()


def test_run_link_check_pending_tolerance_and_ignore_rule(tmp_path: Path) -> None:
    connection = connect_sqlite(tmp_path / "crawl_pending.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.add_page_result(run_id, "https://cardano.org", 0, 200, True)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://temp.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://ignoreme.example/path", is_internal=False)

    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["meta"]["job_id"] = "cardano.org"
    config["link_check"]["enabled"] = True
    config["link_check"]["retry_count"] = 0
    config["link_check"]["tolerance"]["by_category"]["server"]["min_consecutive_failures"] = 2
    config["link_check"]["tolerance"]["by_category"]["server"]["min_age_days"] = 2

    repo.add_link_ignore_rule(
        job_id="cardano.org",
        match_type="contains",
        pattern="ignoreme.example",
        reason="known false positive",
    )

    checker = FakeChecker(
        {
            "https://temp.example": [HttpCheckResult(status_code=503, ok=False, error_message="HTTP Error 503")],
            "https://ignoreme.example/path": [HttpCheckResult(status_code=404, ok=False, error_message="HTTP Error 404")],
        }
    )
    seen_results: list[tuple[str, int | None, bool]] = []

    summary = run_link_check(
        config=config,
        repository=repo,
        checker=checker,
        run_id=run_id,
        result_hook=lambda url, result: seen_results.append((url, result.status_code, result.ok)),
    )
    assert summary.checked == 2
    assert summary.failed == 2
    assert summary.ignored == 1
    assert summary.pending_tolerance == 1
    assert summary.reportable_failures == 0
    assert summary.skipped == 2
    assert seen_results == []
    stored = connection.execute(
        """
        SELECT target_url, decision_state
        FROM link_check_results
        ORDER BY target_url ASC
        """
    ).fetchall()
    assert [tuple(row) for row in stored] == [
        ("https://ignoreme.example/path", "ignored"),
        ("https://temp.example", "pending_tolerance"),
    ]
    connection.close()


def test_run_link_check_ignores_configured_http_status(tmp_path: Path) -> None:
    connection = connect_sqlite(tmp_path / "crawl_http_status_ignore.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.add_page_result(run_id, "https://cardano.org", 0, 200, True)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://cf.example", is_internal=False)

    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["meta"]["job_id"] = "cardano.org"
    config["link_check"]["enabled"] = True
    config["link_check"]["retry_count"] = 0
    config["link_check"]["http_status"] = [403]

    checker = FakeChecker(
        {
            "https://cf.example": [HttpCheckResult(status_code=403, ok=False, error_message="HTTP Error 403")],
        }
    )
    seen_results: list[tuple[str, int | None, bool]] = []

    summary = run_link_check(
        config=config,
        repository=repo,
        checker=checker,
        run_id=run_id,
        result_hook=lambda url, result: seen_results.append((url, result.status_code, result.ok)),
    )
    assert summary.checked == 1
    assert summary.failed == 1
    assert summary.ignored == 1
    assert summary.pending_tolerance == 0
    assert summary.reportable_failures == 0
    assert summary.skipped == 1
    assert seen_results == []
    stored = connection.execute(
        """
        SELECT decision_state, decision_reason
        FROM link_check_results
        WHERE target_url = 'https://cf.example'
        """
    ).fetchone()
    assert stored is not None
    assert stored["decision_state"] == "ignored"
    assert stored["decision_reason"] == "link_check.http_status:403"
    connection.close()


def test_run_link_check_stops_early_when_max_reportable_reached(tmp_path: Path) -> None:
    connection = connect_sqlite(tmp_path / "crawl_max_reportable.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    run_id = repo.create_run("cardano.org")
    repo.add_page_result(run_id, "https://cardano.org", 0, 200, True)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://a.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://b.example", is_internal=False)
    repo.add_link(run_id=run_id, source_url="https://cardano.org", target_url="https://c.example", is_internal=False)

    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["meta"]["job_id"] = "cardano.org"
    config["link_check"]["enabled"] = True
    config["link_check"]["retry_count"] = 0
    config["link_check"]["tolerance"]["by_category"]["client"]["min_consecutive_failures"] = 1
    config["link_check"]["tolerance"]["by_category"]["client"]["min_age_days"] = 0

    checker = FakeChecker(
        {
            "https://a.example": [HttpCheckResult(status_code=404, ok=False, error_message="HTTP Error 404")],
            "https://b.example": [HttpCheckResult(status_code=404, ok=False, error_message="HTTP Error 404")],
            "https://c.example": [HttpCheckResult(status_code=404, ok=False, error_message="HTTP Error 404")],
        }
    )

    summary = run_link_check(
        config=config,
        repository=repo,
        checker=checker,
        run_id=run_id,
        max_reportable_failures=1,
    )
    assert summary.reportable_failures == 1
    assert summary.checked == 1
    assert checker.call_count("https://a.example") == 1
    assert checker.call_count("https://b.example") == 0
    assert checker.call_count("https://c.example") == 0
    connection.close()
