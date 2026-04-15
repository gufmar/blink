from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app


def test_crawl_explore_runs_with_fake_renderer(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    db_path = tmp_path / "explore.db"

    class FakeRenderer:
        def __init__(
            self,
            navigation_timeout_seconds: int,
            network_idle_seconds: int,
            playwright_wait_seconds: int,
            browser_settings,
            observability,
            artifacts_dir=None,
        ) -> None:
            self._args = (navigation_timeout_seconds, network_idle_seconds, playwright_wait_seconds)
            self._browser_settings = browser_settings
            self.opened = False
            self.closed = False

        def open_session(self) -> None:
            self.opened = True

        def close_session(self) -> None:
            self.closed = True

        def render(self, url: str):  # noqa: ANN001
            from app.render.playwright_client import RenderResult

            return RenderResult(
                requested_url=url,
                url=url,
                status_code=200,
                html="<html><body><a href='https://external.example/a'>a</a></body></html>",
                response_headers={},
                challenge_detected=False,
                request_failures=[],
                console_messages=[],
                screenshot_path=None,
                html_snapshot_path=None,
            )

    monkeypatch.setattr("app.commands.crawl.PlaywrightRenderer", FakeRenderer)
    result = runner.invoke(
        app,
        [
            "crawl",
            "explore",
            "--job",
            "jobs/cardano.org.job.json",
            "--db",
            str(db_path),
            "--max-pages",
            "1",
            "--max-runtime-minutes",
            "1",
            "--progress-every",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "Crawl explore complete:" in result.output
    assert "challenged=" in result.output
