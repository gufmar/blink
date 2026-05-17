from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.schedule.service import BlinkSchedulerService


def _write_job(tmp_path: Path, name: str, *, link_check_enabled: bool) -> None:
    root = Path(__file__).resolve().parents[2]
    shutil.copy(root / "jobs" / "_default.job.json", tmp_path / f"{name}.job.json")
    data = json.loads((tmp_path / f"{name}.job.json").read_text(encoding="utf-8"))
    data["meta"]["job_id"] = name
    data["meta"]["enabled"] = True
    data["notifications"]["enabled"] = False
    data["schedule"]["link_check"]["enabled"] = link_check_enabled
    (tmp_path / f"{name}.job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_sync_registers_link_check_after_config_change(tmp_path: Path) -> None:
    _write_job(tmp_path, "syncjob", link_check_enabled=False)
    svc = BlinkSchedulerService(tmp_path)

    before = svc.build_schedule_payload()
    assert [t for t in before["tasks"] if t["task_type"] == "link_check"] == []

    data = json.loads((tmp_path / "syncjob.job.json").read_text(encoding="utf-8"))
    data["schedule"]["link_check"]["enabled"] = True
    (tmp_path / "syncjob.job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    after = svc.build_schedule_payload()
    link_tasks = [t for t in after["tasks"] if t["task_type"] == "link_check"]
    assert len(link_tasks) == 1
    assert link_tasks[0]["runtime"]["next_run_at"]
    assert svc._scheduler.get_job("blink_sched:syncjob:link_check") is not None
