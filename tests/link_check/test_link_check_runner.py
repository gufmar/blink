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

    summary = run_link_check(
        config=config,
        repository=repo,
        checker=checker,
        run_id=run_id,
        result_hook=lambda url, result: seen_results.append((url, result.status_code, result.ok)),
    )
    assert summary.crawl_run_id == run_id
    assert summary.checked == 2
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.errored == 0
    assert checker.call_count("https://ok.example") == 2
    assert checker.call_count("https://bad.example") == 2
    assert len(seen_results) == 2
    assert any(url == "https://bad.example" and ok is False for url, _, ok in seen_results)

    rows = connection.execute(
        "SELECT target_url, ok, status_code FROM link_check_results ORDER BY id ASC"
    ).fetchall()
    assert len(rows) == 2
    connection.close()
