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


def test_dashboard_ok(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Blink schedules" in r.text
