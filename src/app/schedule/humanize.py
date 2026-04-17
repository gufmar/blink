"""Human-readable formatting of schedule configuration."""

from __future__ import annotations

from app.models.job_config import ScheduleConfig, ScheduleTaskConfig


def describe_task(task: ScheduleTaskConfig, *, label: str) -> list[tuple[str, str]]:
    """Return key/value rows for one schedule task."""
    rows: list[tuple[str, str]] = [(f"{label} enabled", str(task["enabled"]))]
    rows.append((f"{label} mode", task["mode"]))
    rows.append((f"{label} expression", task["expression"]))
    rows.append((f"{label} jitter_seconds", str(task["jitter_seconds"])))
    rows.append((f"{label} max_runtime_seconds", str(task["max_runtime_seconds"])))
    rows.append((f"{label} startup_delay_seconds", str(task["startup_delay_seconds"])))
    return rows


def describe_schedule(schedule: ScheduleConfig) -> list[tuple[str, str]]:
    """Return flat key/value rows describing the full schedule section."""
    rows: list[tuple[str, str]] = [
        ("timezone", schedule["timezone"]),
        ("overlap_policy", schedule["overlap_policy"]),
    ]
    rows.extend(describe_task(schedule["crawl"], label="crawl"))
    rows.extend(describe_task(schedule["link_check"], label="link_check"))
    if schedule["maintenance_windows"]:
        for idx, win in enumerate(schedule["maintenance_windows"]):
            rows.append((f"maintenance[{idx}] name", win["name"]))
            rows.append((f"maintenance[{idx}] cron", win["cron"]))
    else:
        rows.append(("maintenance_windows", "(none)"))
    return rows
