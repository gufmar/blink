from __future__ import annotations

from pathlib import Path

from app.config.loader import load_effective_job_config
from app.crawl.runner import run_crawl
from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.render.playwright_client import RenderError, RenderResult


class FakeRenderer:
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.opened = False
        self.closed = False

    def open_session(self) -> None:
        self.opened = True

    def close_session(self) -> None:
        self.closed = True

    def render(self, url: str) -> RenderResult:
        if url not in self._pages:
            raise RenderError(f"Missing fixture for {url}")
        return RenderResult(
            requested_url=url,
            url=url,
            status_code=200,
            html=self._pages[url],
            response_headers={},
            challenge_detected=False,
            request_failures=[],
            console_messages=[],
            screenshot_path=None,
            html_snapshot_path=None,
        )


def test_run_crawl_bounded_and_persists(tmp_path: Path) -> None:
    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["target"]["base_url"] = "https://example.org"
    config["target"]["allowed_domains"] = ["example.org"]
    config["target"]["start_urls"] = ["https://example.org"]
    config["crawl"]["request_delay_seconds"] = 0
    config["crawl"]["max_depth"] = 2
    config["content"]["history_keep"] = 5
    config["content"]["store_rendered_html"] = False

    renderer = FakeRenderer(
        {
            "https://example.org": """
                <html><body>
                <a href="/a">A</a>
                <a href="/a">A2</a>
                <a href="https://external.example/x">X</a>
                </body></html>
            """,
            "https://example.org/a": "<html><body>hello</body></html>",
        }
    )

    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    statuses: list[str] = []
    summary = run_crawl(
        config=config,
        repository=repo,
        renderer=renderer,
        max_pages_override=2,
        status_hook=statuses.append,
    )

    assert summary.pages_visited == 2
    assert summary.pages_failed == 0
    assert summary.links_discovered == 2
    assert summary.unique_external_urls == 1
    assert summary.challenged_pages == 0
    assert summary.non_2xx_pages == 0
    assert summary.request_failures == 0
    assert renderer.opened is True
    assert renderer.closed is True
    assert statuses
    assert "Crawling depth=0 progress=" in statuses[0]

    pages_count = connection.execute("SELECT COUNT(*) FROM crawl_pages").fetchone()[0]
    links_count = connection.execute("SELECT COUNT(*) FROM crawl_links").fetchone()[0]
    assert pages_count == 2
    assert links_count == 2
    edge_count = connection.execute("SELECT COUNT(*) FROM run_page_external_links").fetchone()[0]
    assert edge_count == 1
    connection.close()


def test_run_crawl_counts_challenges_and_non_2xx(tmp_path: Path) -> None:
    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["target"]["base_url"] = "https://example.org"
    config["target"]["allowed_domains"] = ["example.org"]
    config["target"]["start_urls"] = ["https://example.org"]
    config["crawl"]["request_delay_seconds"] = 0
    config["crawl"]["max_depth"] = 0

    class ChallengeRenderer(FakeRenderer):
        def render(self, url: str) -> RenderResult:
            return RenderResult(
                requested_url=url,
                url="https://example.org/challenge",
                status_code=429,
                html="<html><body>challenge</body></html>",
                response_headers={"x-vercel-mitigated": "challenge"},
                challenge_detected=True,
                request_failures=["https://example.org/script.js -> net::ERR_ABORTED"],
                console_messages=[],
                screenshot_path="/tmp/fail.png",
                html_snapshot_path="/tmp/fail.html",
            )

    renderer = ChallengeRenderer({})
    connection = connect_sqlite(tmp_path / "crawl.db")
    initialize_schema(connection)
    repo = CrawlRepository(connection)
    summary = run_crawl(config=config, repository=repo, renderer=renderer, max_pages_override=1)
    assert summary.challenged_pages == 1
    assert summary.non_2xx_pages == 1
    assert summary.request_failures == 1
    connection.close()
