"""Maintenance window evaluation (cron-based suppression of task starts)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from croniter.croniter import CroniterBadCronError

from app.models.job_config import ScheduleConfig


def is_start_blocked_by_maintenance(schedule: ScheduleConfig, at: datetime | None = None) -> bool:
    """Return True if a scheduled task should not **start** at time ``at``.

    Each ``maintenance_windows[]`` entry uses a 5-field cron expression evaluated in
    ``schedule.timezone``. If **any** window's cron matches the current time
    (per :func:`croniter.croniter.match`), starts are suppressed.
    """
    when = at or datetime.now(tz=ZoneInfo("UTC"))
    try:
        tz = ZoneInfo(schedule["timezone"])
    except Exception:
        tz = ZoneInfo("UTC")
    local = when.astimezone(tz)

    for window in schedule["maintenance_windows"]:
        cron_ex = str(window.get("cron") or "").strip()
        if not cron_ex:
            continue
        try:
            if croniter.match(cron_ex, local):
                return True
        except CroniterBadCronError:
            continue
    return False
