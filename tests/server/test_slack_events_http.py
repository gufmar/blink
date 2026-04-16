from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.runtime.job_paths import JobPaths
from app.server.asgi import build_app
from starlette.testclient import TestClient


def _sign(secret: str, body: bytes, ts: str | None = None) -> tuple[str, str]:
    ts = ts or str(int(time.time()))
    basestring = f"v0:{ts}:{body.decode('utf-8')}"
    digest = hmac.new(secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256).hexdigest()
    return ts, f"v0={digest}"


def _prepare_job_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "zzz") -> Path:
    """Copy default job template, set meta.job_id and notifications.slack_signing_secret_env."""
    root = Path(__file__).resolve().parents[2]
    default_src = root / "jobs" / "_default.job.json"
    job_path = tmp_path / f"{name}.job.json"
    shutil.copy(default_src, job_path)
    data = json.loads(job_path.read_text(encoding="utf-8"))
    data["meta"]["job_id"] = name
    data["meta"]["name"] = "Test Job"
    data["notifications"]["enabled"] = True
    data["notifications"]["slack_signing_secret_env"] = "TEST_SLACK_SIGNING_SECRET"
    job_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    db_path = tmp_path / f"{name}.sqlite3"

    def _fake_build_paths(job_id: str, root_dir: Path | None = None, on_date=None):  # noqa: ANN001
        _ = root_dir
        _ = on_date
        _ = job_id
        return JobPaths(
            job_root=tmp_path,
            db_dir=tmp_path,
            db_path=db_path,
            logs_dir=tmp_path,
            log_path=tmp_path / "log.txt",
            artifacts_dir=tmp_path / "art",
            reports_dir=tmp_path / "rep",
        )

    monkeypatch.setattr("app.server.asgi.build_job_paths", _fake_build_paths)
    return job_path


def test_health_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_job_file(tmp_path, monkeypatch)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/notifications/slack/health").json() == {"status": "ok"}


def test_url_verification_returns_challenge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_job_file(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_SLACK_SIGNING_SECRET", "mysecret")
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    body_dict = {"type": "url_verification", "token": "x", "challenge": "challenge-abc-123"}
    body = json.dumps(body_dict).encode("utf-8")
    ts, sig = _sign("mysecret", body)
    r = client.post(
        "/notifications/slack/job/zzz",
        content=body,
        headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig},
    )
    assert r.status_code == 200
    assert r.json() == {"challenge": "challenge-abc-123"}


def test_invalid_signature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_job_file(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_SLACK_SIGNING_SECRET", "mysecret")
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    body = b'{"type":"url_verification","challenge":"x"}'
    r = client.post(
        "/notifications/slack/job/zzz",
        content=body,
        headers={"X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=deadbeef"},
    )
    assert r.status_code == 401


def test_unknown_job_returns_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_job_file(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_SLACK_SIGNING_SECRET", "s")
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    body = b'{"type":"url_verification","challenge":"c"}'
    ts, sig = _sign("s", body)
    r = client.post(
        "/notifications/slack/job/doesnotexist",
        content=body,
        headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig},
    )
    assert r.status_code == 404


def test_event_callback_applies_when_patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_job_file(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_SLACK_SIGNING_SECRET", "sec")
    mock = MagicMock(return_value=(True, None))
    monkeypatch.setattr("app.server.asgi.apply_inbound_slack_from_envelope", mock)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    envelope = {
        "type": "event_callback",
        "token": "x",
        "team_id": "T",
        "event": {
            "type": "reaction_added",
            "user": "U1",
            "reaction": "x",
            "item": {"type": "message", "channel": "C123", "ts": "1.1"},
        },
    }
    body = json.dumps(envelope).encode("utf-8")
    ts, sig = _sign("sec", body)
    r = client.post(
        "/notifications/slack/job/zzz",
        content=body,
        headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert mock.called


def test_notifications_config_must_have_signing_secret_env_or_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    shutil.copy(root / "jobs" / "_default.job.json", tmp_path / "nosec.job.json")
    data = json.loads((tmp_path / "nosec.job.json").read_text(encoding="utf-8"))
    data["meta"]["job_id"] = "nosec"
    data["notifications"]["slack_signing_secret_env"] = "MISSING_ENV_VAR_XYZ"
    (tmp_path / "nosec.job.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.delenv("MISSING_ENV_VAR_XYZ", raising=False)
    app = build_app(jobs_root=tmp_path)
    client = TestClient(app)
    body = b'{"type":"url_verification","challenge":"c"}'
    # Cannot sign without secret — test unconfigured secret path by using wrong approach:
    # First set a temp secret so signature passes, but we need 500 from missing secret.
    # Actually: resolve_notifications_signing_secret returns None -> 500 before verify.
    body_dict = {"type": "url_verification", "challenge": "c"}
    body = json.dumps(body_dict).encode("utf-8")
    # sign with dummy — handler checks secret before verify; order is load config, secret None -> 500
    r = client.post("/notifications/slack/job/nosec", content=body, headers={"X-Slack-Request-Timestamp": "1", "X-Slack-Signature": "v0=x"})
    assert r.status_code == 500
    assert r.json()["error"] == "signing_secret_unconfigured"
