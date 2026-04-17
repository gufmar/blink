"""Build APScheduler triggers from schedule task config."""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.models.job_config import ScheduleTaskConfig
from app.schedule.expression import parse_interval_expression

TaskMode = Literal["interval", "cron"]


def build_trigger(
    task: ScheduleTaskConfig,
    *,
    timezone_name: str,
) -> IntervalTrigger | CronTrigger:
    """Return an APScheduler trigger for ``task`` using ``timezone_name`` for cron."""
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    mode = task["mode"]
    expr = task["expression"].strip()
    jitter = max(0, int(task["jitter_seconds"]))

    if mode == "interval":
        delta = parse_interval_expression(expr)
        seconds = int(delta.total_seconds())
        if seconds <= 0:
            raise ValueError("interval must be positive")
        return IntervalTrigger(seconds=seconds, timezone=tz, jitter=jitter)

    if mode == "cron":
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"cron expression must have 5 fields, got {len(fields)}: {expr!r}")
        minute, hour, day, month, day_of_week = fields
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=tz,
            jitter=jitter,
        )

    raise ValueError(f"unsupported schedule mode: {mode!r}")


def interval_seconds(task: ScheduleTaskConfig) -> int:
    """Return interval length in seconds for ``interval`` mode tasks."""
    if task["mode"] != "interval":
        raise ValueError("interval_seconds applies only to interval mode")
    delta = parse_interval_expression(task["expression"])
    return max(1, int(delta.total_seconds()))
