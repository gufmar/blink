from __future__ import annotations

from pathlib import Path

import pytest

from app.config.jobs_root import BLINK_JOBS_ROOT_ENV, resolve_jobs_root


def test_resolve_jobs_root_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setenv(BLINK_JOBS_ROOT_ENV, str(jobs))
    assert resolve_jobs_root(None) == jobs.resolve()


def test_resolve_jobs_root_cli_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    cli = tmp_path / "cli"
    cli.mkdir()
    monkeypatch.setenv(BLINK_JOBS_ROOT_ENV, str(other))
    assert resolve_jobs_root(cli) == cli.resolve()
