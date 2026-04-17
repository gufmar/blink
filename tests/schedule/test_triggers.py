from __future__ import annotations

from app.models.job_config import ScheduleTaskConfig
from app.schedule.triggers import build_trigger


def test_build_interval_trigger() -> None:
    task: ScheduleTaskConfig = {
        "enabled": True,
        "mode": "interval",
        "expression": "1h",
        "jitter_seconds": 5,
        "max_runtime_seconds": 100,
        "startup_delay_seconds": 0,
    }
    tr = build_trigger(task, timezone_name="UTC")
    assert "interval" in str(tr).lower()


def test_build_cron_trigger() -> None:
    task: ScheduleTaskConfig = {
        "enabled": True,
        "mode": "cron",
        "expression": "0 */6 * * *",
        "jitter_seconds": 0,
        "max_runtime_seconds": 100,
        "startup_delay_seconds": 0,
    }
    tr = build_trigger(task, timezone_name="Europe/Berlin")
    assert "cron" in str(tr).lower()
