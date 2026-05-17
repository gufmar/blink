from __future__ import annotations

import json
import shutil
from pathlib import Path

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


def test_dashboard_hides_jobs_root_and_shows_auto_refresh(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "<strong>jobs_root</strong>" not in page.text
    assert "jobs_root ·" not in page.text
    assert "Auto-refresh (15s)" in page.text
    assert "scheduled crawls" in page.text
    assert "scheduled link checks" in page.text
    assert "service status" in page.text
    assert 'id="dashboardAutoRefresh"' in page.text


def test_admin_runtime_page_available_without_auth(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    page = client.get("/dashboard/admin/runtime")
    assert page.status_code == 200
    assert "Runtime &amp; environment" in page.text
    assert "Blink operations" in page.text
    assert "Slack health" in page.text
    assert "Schedule JSON" in page.text
    assert "< Dashboard" in page.text
    assert str(tmp_path.resolve()) in page.text
