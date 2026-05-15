from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.repository import AuthRepository
from app.server.asgi import build_app
from app.server.global_auth_db import connect_server_db


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


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLINK_AUTH_PASSWORD", "1")
    monkeypatch.setenv("BLINK_SESSION_SECRET", "test-secret-key-for-pytest-only")
    monkeypatch.delenv("BLINK_AUTH_GOOGLE", raising=False)


def test_dashboard_open_when_auth_disabled(tmp_path: Path) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    r = client.get("/dashboard")
    assert r.status_code == 200


def test_api_requires_login_when_auth_enabled(tmp_path: Path, auth_env: None) -> None:
    _minimal_job(tmp_path)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    r = client.get("/api/schedule")
    assert r.status_code == 401
    assert r.json()["error"] == "authentication_required"


def test_password_login_and_session(tmp_path: Path, auth_env: None) -> None:
    _minimal_job(tmp_path)
    conn = connect_server_db(tmp_path)
    try:
        repo = AuthRepository(conn)
        uid = repo.create_user(email="admin@example.com", password_hash=hash_password("hunter2pass"), is_global_admin=True)
        assert uid > 0
    finally:
        conn.close()

    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    r = client.post(
        "/auth/login",
        data={"email": "admin@example.com", "password": "hunter2pass", "next": "/dashboard"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    r2 = client.get("/dashboard")
    assert r2.status_code == 200
    assert "Signed in as" in r2.text


def test_job_forbidden_without_role(tmp_path: Path, auth_env: None) -> None:
    _minimal_job(tmp_path, "allowed")
    _minimal_job(tmp_path, "secret")
    conn = connect_server_db(tmp_path)
    try:
        repo = AuthRepository(conn)
        repo.create_user(email="w@example.com", password_hash=hash_password("password1234"))
        repo.set_job_role(1, "allowed", "watcher")
    finally:
        conn.close()

    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    client.post("/auth/login", data={"email": "w@example.com", "password": "password1234"})
    r = client.get("/api/results/jobs/secret/runs")
    assert r.status_code == 403


def test_login_redirect_without_double_base_path(tmp_path: Path, auth_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_job(tmp_path)
    monkeypatch.setenv("BLINK_PUBLIC_BASE_URL", "https://hey.cardano.org/blink")
    app = build_app(jobs_root=tmp_path, route_base_path="/blink")
    client = TestClient(app, root_path="/blink")
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert "/blink/blink" not in location
    assert location.startswith("/blink/auth/login")
    assert "next=%2Fblink%2Fdashboard" in location


def test_setup_token_with_route_base_path(tmp_path: Path, auth_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth.config import AuthConfig
    from app.server.auth_routes import issue_password_token

    monkeypatch.setenv("BLINK_PUBLIC_BASE_URL", "https://hey.cardano.org")
    monkeypatch.setenv("BLINK_ROUTE_BASE_PATH", "/blink")
    cfg = AuthConfig.from_env()
    conn = connect_server_db(tmp_path)
    try:
        repo = AuthRepository(conn)
        uid = repo.create_user(email="u@example.com")
        raw, link = issue_password_token(
            repo,
            user_id=uid,
            purpose="password_setup",
            session_secret=cfg.session_secret or "",
            public_url=cfg.public_base_url,
            route_base_path=cfg.route_base_path,
        )
        assert link.startswith("https://hey.cardano.org/blink/auth/set-password?token=")
        app = build_app(jobs_root=tmp_path, route_base_path="/blink")
        client = TestClient(app, root_path="/blink")
        r = client.get(f"/auth/set-password?token={raw}")
        assert r.status_code == 200
        assert "Set password" in r.text
    finally:
        conn.close()


def test_blink_user_add_cli(tmp_path: Path, auth_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    from typer.testing import CliRunner

    from app.cli.main import app as cli_app

    runner = CliRunner()
    result = runner.invoke(cli_app, ["user", "add", "new@example.com", "--jobs-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Created user" in result.output
    conn = connect_server_db(tmp_path)
    try:
        repo = AuthRepository(conn)
        assert repo.get_user_by_email("new@example.com") is not None
    finally:
        conn.close()
