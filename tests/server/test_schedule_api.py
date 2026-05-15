from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.server.asgi import build_app


def _minimal_job(tmp_path: Path, name: str = "zzz") -> None:
    root = Path(__file__).resolve().parents[2]
    default_src = root / "jobs" / "_default.job.json"
    job_path = tmp_path / f"{name}.job.json"
    shutil.copy(default_src, job_path)
    data = json.loads(job_path.read_text(encoding="utf-8"))
    data["meta"]["job_id"] = name
    data["meta"]["enabled"] = True
    data["notifications"]["enabled"] = False
    job_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_api_schedule_returns_payload(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    r = client.get("/api/schedule")
    assert r.status_code == 200
    payload = r.json()
    assert "jobs_root" in payload
    assert payload["jobs_root"] == str(tmp_path.resolve())
    assert "tasks" in payload and isinstance(payload["tasks"], list)
    assert "scheduler_running" in payload
    assert payload.get("scheduler", {}).get("max_concurrent_tasks") == 0


def test_dashboard_ok(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Blink schedules" in r.text
    assert "/api/schedule" in r.text
    assert "/dashboard/results" in r.text
    assert "panel-scroll" in r.text
    assert "line-dot" in r.text
    assert "job-history-link" in r.text


def test_dashboard_shows_last_run_when_schedule_disabled(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from app.persistence.repository import CrawlRepository
    from app.persistence.sqlite import connect_sqlite, initialize_schema

    _minimal_job(tmp_path)
    job_path = tmp_path / "zzz.job.json"
    data = json.loads(job_path.read_text(encoding="utf-8"))
    data["schedule"]["crawl"]["enabled"] = False
    data["schedule"]["link_check"]["enabled"] = False
    job_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    db_path = tmp_path / "data" / "zzz" / "db" / "zzz.sqlite3"
    conn = connect_sqlite(db_path)
    initialize_schema(conn)
    repo = CrawlRepository(conn)
    run_id = repo.create_run("zzz")
    repo.add_page_result(run_id=run_id, url="https://example.org", depth=0, status_code=200, ok=True)
    repo.finish_run(run_id=run_id, pages_visited=1, pages_failed=0, links_discovered=0)
    lc_id = repo.create_link_check_run(
        job_id="zzz",
        based_on_crawl_run_id=run_id,
        started_at=datetime.now(tz=UTC).isoformat(),
    )
    repo.finish_link_check_run(
        link_check_run_id=lc_id,
        finished_at=datetime.now(tz=UTC).isoformat(),
        checked_total=1,
        passed_total=1,
        failed_total=0,
        errored_total=0,
        ignored_total=0,
        pending_tolerance_total=0,
        reportable_failures_total=0,
    )
    conn.close()

    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "last crawl" in page.text
    assert "last check" in page.text
    assert f"/dashboard/results/zzz/runs/{run_id}?task_type=crawl" in page.text
    assert f"/dashboard/results/zzz/runs/{lc_id}?task_type=link_check" in page.text
    assert "not scheduled" in page.text
    assert "crawls" not in page.text
    assert "link-checks" not in page.text


def test_dashboard_links_honor_proxy_root_path(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app, root_path="/blink")
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "/blink/dashboard/results" in r.text
    assert "/blink/api/schedule" in r.text


def test_dashboard_links_honor_configured_base_path(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path, route_base_path="/blink")
    client = TestClient(app)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "/blink/dashboard/results" in r.text
    assert "/blink/api/schedule" in r.text
