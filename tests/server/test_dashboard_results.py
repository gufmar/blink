from __future__ import annotations

import json
import shutil
from pathlib import Path

from starlette.testclient import TestClient

from datetime import UTC, date, datetime

from app.persistence.repository import CrawlRepository
from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.server.asgi import build_app


def _setup_job_with_data(tmp_path: Path, job_id: str = "zzz") -> tuple[Path, int, int]:
    root = Path(__file__).resolve().parents[2]
    default_src = root / "jobs" / "_default.job.json"
    job_path = tmp_path / f"{job_id}.job.json"
    shutil.copy(default_src, job_path)
    data = json.loads(job_path.read_text(encoding="utf-8"))
    data["meta"]["job_id"] = job_id
    data["meta"]["enabled"] = True
    data["meta"]["name"] = "Dashboard Test Job"
    data["notifications"]["enabled"] = False
    job_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    db_path = tmp_path / "data" / job_id / "db" / f"{job_id}.sqlite3"
    conn = connect_sqlite(db_path)
    initialize_schema(conn)
    repo = CrawlRepository(conn)
    old_run = repo.create_run(job_id)
    repo.add_page_result(run_id=old_run, url="https://example.org", depth=0, status_code=200, ok=True)
    repo.add_link(
        run_id=old_run,
        source_url="https://example.org",
        target_url="https://broken.example.net/old",
        is_internal=False,
        anchor_text="Broken old",
    )
    old_target = repo.list_links_for_check(old_run, limit=1)[0]
    repo.add_link_check_result(
        crawl_link_id=old_target.link_id,
        crawl_run_id=old_run,
        link_check_run_id=None,
        target_url=old_target.target_url,
        status_code=500,
        ok=False,
        error_message="server",
        error_category="server",
    )
    repo.finish_run(run_id=old_run, pages_visited=1, pages_failed=0, links_discovered=1)

    run_id = repo.create_run(job_id)
    link_check_run_id = repo.create_link_check_run(
        job_id=job_id,
        based_on_crawl_run_id=run_id,
        started_at=datetime.now(tz=UTC).isoformat(),
    )
    repo.add_page_result(
        run_id=run_id,
        url="https://example.org",
        depth=0,
        status_code=200,
        ok=True,
        main_text="Hello crawl text",
    )
    repo.add_page_result(
        run_id=run_id,
        url="https://example.org/failure",
        depth=1,
        status_code=503,
        ok=False,
        error_message="upstream error",
    )
    repo.add_link(
        run_id=run_id,
        source_url="https://example.org",
        target_url="https://broken.example.net",
        is_internal=False,
        anchor_text="Broken external",
    )
    repo.add_link(
        run_id=run_id,
        source_url="https://example.org",
        target_url="https://ignored.example.net",
        is_internal=False,
        anchor_text="Ignored external",
    )
    check_targets = {row.target_url: row for row in repo.list_links_for_check(run_id, limit=10)}
    check_target = check_targets["https://broken.example.net"]
    repo.add_link_check_result(
        crawl_link_id=check_target.link_id,
        crawl_run_id=run_id,
        link_check_run_id=link_check_run_id,
        target_url=check_target.target_url,
        status_code=404,
        ok=False,
        error_message="not found",
        error_category="client",
    )
    ignored_target = check_targets["https://ignored.example.net"]
    repo.add_link_check_result(
        crawl_link_id=ignored_target.link_id,
        crawl_run_id=run_id,
        link_check_run_id=link_check_run_id,
        target_url=ignored_target.target_url,
        status_code=403,
        ok=False,
        error_message="cloudflare",
        error_category="client",
        decision_state="ignored",
        decision_reason="link_check.ignore.http_status:403",
    )
    repo.add_link_ignore_rule(
        job_id=job_id,
        match_type="contains",
        pattern="broken.example.net",
        reason="allowed for test",
    )
    repo.finish_run(run_id=run_id, pages_visited=2, pages_failed=1, links_discovered=1)
    repo.finish_link_check_run(
        link_check_run_id=link_check_run_id,
        finished_at=datetime.now(tz=UTC).isoformat(),
        checked_total=2,
        passed_total=0,
        failed_total=2,
        errored_total=0,
        ignored_total=1,
        pending_tolerance_total=0,
        reportable_failures_total=1,
    )
    conn.close()
    return job_path, run_id, link_check_run_id


def test_api_results_endpoints(tmp_path: Path) -> None:
    _, run_id, link_check_run_id = _setup_job_with_data(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)

    jobs = client.get("/api/results/jobs")
    assert jobs.status_code == 200
    jobs_payload = jobs.json()
    assert jobs_payload["jobs"][0]["job_id"] == "zzz"
    assert jobs_payload["jobs"][0]["latest_run"]["run_id"] == run_id
    assert len(jobs_payload["task_rows"]) == 2
    task_types = {row["task_type"] for row in jobs_payload["task_rows"]}
    assert task_types == {"crawl", "link_check"}

    runs = client.get("/api/results/jobs/zzz/runs")
    assert runs.status_code == 200
    runs_payload = runs.json()
    assert runs_payload["job"]["job_id"] == "zzz"
    crawl_rows = [r for r in runs_payload["runs"] if r.get("task_type") == "crawl"]
    assert crawl_rows and crawl_rows[0]["run_id"] == run_id
    link_check_runs = client.get("/api/results/jobs/zzz/runs?task_type=link_check")
    assert link_check_runs.status_code == 200
    link_check_runs_payload = link_check_runs.json()
    assert link_check_runs_payload["task_type"] == "link_check"
    assert link_check_runs_payload["runs"][0]["based_on_crawl_run_id"] == run_id

    detail = client.get(f"/api/results/jobs/zzz/runs/{run_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["run"]["run_id"] == run_id
    assert detail_payload["failed_links"][0]["target_url"] == "https://broken.example.net"
    assert "https://example.org" in detail_payload["failed_links"][0]["source_pages"]
    assert detail_payload["failed_overview"]["failed_total"] == 1
    assert detail_payload["failed_overview"]["ignored_total"] == 1
    assert detail_payload["ignored_links"][0]["target_url"] == "https://ignored.example.net"
    structure = client.get(f"/api/results/jobs/zzz/runs/{run_id}/structure")
    assert structure.status_code == 200
    structure_payload = structure.json()
    assert structure_payload["job_id"] == "zzz"
    assert structure_payload["run_id"] == run_id
    assert structure_payload["metric"] == "external_count"
    assert structure_payload["external_mode"] == "none"
    assert structure_payload["nodes"]["name"] == "/"
    assert structure_payload["nodes"]["node_kind"] == "internal_root"
    assert "page_content_api" in structure_payload
    assert "/page-main-text" in structure_payload["page_content_api"]["href"]
    assert structure_payload["task_type"] == "crawl"
    page_text = client.get(
        "/api/results/jobs/zzz/runs/"
        + str(run_id)
        + "/page-main-text?url=https%3A%2F%2Fexample.org&task_type=crawl"
    )
    assert page_text.status_code == 200
    assert page_text.json()["main_text"] == "Hello crawl text"
    missing = client.get(f"/api/results/jobs/zzz/runs/{run_id}/page-main-text?task_type=crawl")
    assert missing.status_code == 400
    structure_with_failed_external = client.get(
        f"/api/results/jobs/zzz/runs/{run_id}/structure?external_mode=failed"
    )
    assert structure_with_failed_external.status_code == 200
    structure_ext_payload = structure_with_failed_external.json()
    raw_json = json.dumps(structure_ext_payload)
    assert structure_ext_payload["external_mode"] == "failed"
    assert "external_domain" in raw_json
    assert "external_url" in raw_json
    structure_with_ignored_external = client.get(
        f"/api/results/jobs/zzz/runs/{run_id}/structure?external_mode=ignored"
    )
    assert structure_with_ignored_external.status_code == 200
    assert structure_with_ignored_external.json()["external_mode"] == "ignored"
    structure_from_link_check = client.get(
        f"/api/results/jobs/zzz/runs/{link_check_run_id}/structure?task_type=link_check"
    )
    assert structure_from_link_check.status_code == 200
    structure_lc_payload = structure_from_link_check.json()
    assert structure_lc_payload["run_id"] == run_id
    assert structure_lc_payload["selected_link_check_run_id"] == link_check_run_id


def test_dashboard_results_pages(tmp_path: Path) -> None:
    _, run_id, link_check_run_id = _setup_job_with_data(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)

    jobs_page = client.get("/dashboard/results")
    assert jobs_page.status_code == 200
    assert "Blink results" in jobs_page.text
    assert "/dashboard/results/zzz" in jobs_page.text

    crawls_page = client.get("/dashboard/results/zzz/crawls")
    assert crawls_page.status_code == 200
    assert "View" in crawls_page.text
    assert "/dashboard/results/zzz/link-checks" in crawls_page.text

    lc_only = client.get("/dashboard/results/zzz/link-checks")
    assert lc_only.status_code == 200
    assert f"?task_type=link_check" in lc_only.text

    job_page = client.get("/dashboard/results/zzz")
    assert job_page.status_code == 200
    assert f"/dashboard/results/zzz/runs/{run_id}" in job_page.text
    assert "Run history" in job_page.text

    log_day = date.today().isoformat()
    log_dir = tmp_path / "data" / "zzz" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{log_day}.log").write_text("blink-test-log-line\n", encoding="utf-8")

    run_page = client.get(f"/dashboard/results/zzz/runs/{run_id}")
    assert run_page.status_code == 200
    assert "run logs" in run_page.text
    assert f"/dashboard/results/zzz/logs/{log_day}" in run_page.text
    assert "Failed link-check results" in run_page.text
    assert "https://broken.example.net" in run_page.text
    assert "https://ignored.example.net" in run_page.text
    assert "Ignored link-check results (latest per target)" in run_page.text
    assert "Run summary" in run_page.text
    assert "< Dashboard" in run_page.text
    assert "< job" in run_page.text
    assert "Apply filters" in run_page.text
    assert "Field" not in run_page.text

    structure_page = client.get(f"/dashboard/results/zzz/runs/{run_id}/structure")
    assert structure_page.status_code == 200
    assert "Radial tidy tree" in structure_page.text
    assert "Website structure JSON" in structure_page.text
    assert "Open run details" in structure_page.text
    assert "page-main-text" in structure_page.text
    assert "id=\"structure-payload\"" in structure_page.text
    assert "id=\"linkCheckRun\"" in structure_page.text
    assert "id=\"externalMode\"" in structure_page.text
    structure_from_link_check_page = client.get(
        f"/dashboard/results/zzz/runs/{link_check_run_id}/structure?task_type=link_check"
    )
    assert structure_from_link_check_page.status_code == 200
    structure_external_page = client.get(f"/dashboard/results/zzz/runs/{run_id}/structure?external_mode=failed")
    assert structure_external_page.status_code == 200
    assert "failed only" in structure_external_page.text
    assert "ignored only" in structure_external_page.text

    history_page = client.get("/dashboard/results/zzz/history")
    assert history_page.status_code == 200
    assert "Task history" in history_page.text
    assert "\u2b11" in history_page.text
    assert f'<span class="mono">{link_check_run_id}</span>' in history_page.text
    assert f"/dashboard/results/zzz/runs/{run_id}?task_type=crawl" in history_page.text
    assert "Download JSON" not in history_page.text or "json" in history_page.text.lower()

    log_ok = client.get(f"/dashboard/results/zzz/logs/{log_day}")
    assert log_ok.status_code == 200
    assert "blink-test-log-line" in log_ok.text
    assert "Download" in log_ok.text
    assert "viewer-pre" in log_ok.text
    log_download = client.get(f"/dashboard/results/zzz/logs/{log_day}?download=1")
    assert log_download.status_code == 200
    assert "blink-test-log-line" in log_download.text
    assert client.get("/dashboard/results/zzz/logs/not-valid").status_code == 404
    assert client.get("/dashboard/results/zzz/logs/2020-01-01").status_code == 404

    run_logs = client.get(f"/dashboard/results/zzz/runs/{run_id}/logs?task_type=crawl")
    assert run_logs.status_code == 200
    assert "blink-test-log-line" in run_logs.text

    reports_dir = tmp_path / "data" / "zzz" / "reports"
    reports_dir.mkdir(parents=True)
    sample_report = reports_dir / f"report_zzz_{log_day}_12-00.json"
    sample_report.write_text('{"ok": true}', encoding="utf-8")
    report_view = client.get(f"/dashboard/results/zzz/reports/{sample_report.name}")
    assert report_view.status_code == 200
    assert "&quot;ok&quot;: true" in report_view.text
    assert "viewer-pre" in report_view.text
    assert "Download JSON" in report_view.text
    report_download = client.get(f"/dashboard/results/zzz/reports/{sample_report.name}?download=1")
    assert report_download.status_code == 200
    assert report_download.json() == {"ok": True}
    assert client.get("/dashboard/results/zzz/reports/evil.json").status_code == 404


def test_dashboard_results_pages_honor_proxy_root_path(tmp_path: Path) -> None:
    _, run_id, _ = _setup_job_with_data(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app, root_path="/blink")

    jobs_page = client.get("/dashboard/results")
    assert jobs_page.status_code == 200
    assert "/blink/dashboard/results/zzz" in jobs_page.text

    job_page = client.get("/dashboard/results/zzz")
    assert job_page.status_code == 200
    assert f"/blink/dashboard/results/zzz/runs/{run_id}?task_type=crawl" in job_page.text
    run_page = client.get(f"/dashboard/results/zzz/runs/{run_id}")
    assert run_page.status_code == 200
    assert f"/blink/dashboard/results/zzz/runs/{run_id}/structure" in run_page.text


def test_dashboard_results_pages_honor_configured_base_path(tmp_path: Path) -> None:
    _, run_id, _ = _setup_job_with_data(tmp_path)
    app = build_app(jobs_root=tmp_path, route_base_path="/blink")
    client = TestClient(app)

    jobs_page = client.get("/dashboard/results")
    assert jobs_page.status_code == 200
    assert "/blink/dashboard/results/zzz" in jobs_page.text

    run_page = client.get(f"/dashboard/results/zzz/runs/{run_id}?include_category=client&exclude_status=403")
    assert run_page.status_code == 200
    assert "/blink/api/results/jobs/zzz/runs/" in run_page.text
    assert "Clear" in run_page.text
    assert "include_status=403" in run_page.text
    assert f"/blink/dashboard/results/zzz/runs/{run_id}/structure" in run_page.text
