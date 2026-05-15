from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.schedule.service import _task_phase_offset_seconds
from app.schedule.triggers import build_trigger


def test_phase_offset_defaults_to_zero() -> None:
    task = {
        "enabled": True,
        "mode": "interval",
        "expression": "1h",
        "jitter_seconds": 0,
        "max_runtime_seconds": 60,
        "startup_delay_seconds": 10,
    }
    assert _task_phase_offset_seconds(task) == 0  # type: ignore[arg-type]


def test_phase_offset_in_start_date() -> None:
    task = {
        "enabled": True,
        "mode": "interval",
        "expression": "1h",
        "jitter_seconds": 0,
        "max_runtime_seconds": 60,
        "startup_delay_seconds": 10,
        "phase_offset_seconds": 90,
    }
    anchor = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    trigger = build_trigger(task, timezone_name="UTC", not_before=anchor)  # type: ignore[arg-type]
    next_run = trigger.get_next_fire_time(None, anchor - timedelta(seconds=1))
    assert next_run == anchor


def test_phase_offset_seconds_parsed() -> None:
    task = {
        "enabled": True,
        "mode": "interval",
        "expression": "1h",
        "jitter_seconds": 0,
        "max_runtime_seconds": 60,
        "startup_delay_seconds": 0,
        "phase_offset_seconds": 120,
    }
    assert _task_phase_offset_seconds(task) == 120  # type: ignore[arg-type]
