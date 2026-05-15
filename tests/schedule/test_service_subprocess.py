from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.schedule.registry import ScheduleRegistryEntry
from app.schedule.service import BlinkSchedulerService


def _job(tmp_path: Path, name: str = "svcjob") -> ScheduleRegistryEntry:
    root = Path(__file__).resolve().parents[2]
    shutil.copy(root / "jobs" / "_default.job.json", tmp_path / f"{name}.job.json")
    data = json.loads((tmp_path / f"{name}.job.json").read_text(encoding="utf-8"))
    data["meta"]["job_id"] = name
    data["meta"]["enabled"] = True
    data["notifications"]["enabled"] = False
    data["schedule"]["crawl"]["enabled"] = True
    data["schedule"]["crawl"]["expression"] = "24h"
    data["schedule"]["link_check"]["enabled"] = False
    (tmp_path / f"{name}.job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    from app.config.loader import load_effective_job_config

    cfg = load_effective_job_config(tmp_path / f"{name}.job.json")
    return ScheduleRegistryEntry(
        job_path=(tmp_path / f"{name}.job.json").resolve(),
        slug=name,
        config=cfg,
    )


def test_execute_task_records_finish(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    entry = _job(tmp_path)
    svc = BlinkSchedulerService(tmp_path)
    mock = MagicMock()
    mock.return_value = MagicMock(returncode=0, stderr="")
    monkeypatch.setattr("app.schedule.service.run_scheduled_task", mock)

    svc._run_task_body(entry, "crawl", entry.config["schedule"]["crawl"])

    mock.assert_called_once()
    st = svc._store.get(entry.job_id, "crawl")
    assert st is not None
    assert st.running is False
    assert st.last_exit_code == 0


def test_execute_task_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import subprocess

    entry = _job(tmp_path)
    svc = BlinkSchedulerService(tmp_path)

    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr("app.schedule.service.run_scheduled_task", boom)

    svc._run_task_body(entry, "crawl", entry.config["schedule"]["crawl"])

    st = svc._store.get(entry.job_id, "crawl")
    assert st is not None
    assert st.last_exit_code == -9
    assert "timeout" in (st.last_error or "").lower()
