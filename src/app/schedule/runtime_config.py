"""Scheduler runtime settings (env / blink serve flags)."""

from __future__ import annotations

import os
from dataclasses import dataclass

BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV = "BLINK_SCHEDULER_MAX_CONCURRENT_TASKS"


@dataclass(frozen=True)
class SchedulerRuntimeConfig:
    """Process-wide scheduler limits shared by crawl and link-check tasks."""

    max_concurrent_tasks: int

    @classmethod
    def from_env(cls) -> SchedulerRuntimeConfig:
        raw = os.getenv(BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV, "").strip()
        if not raw:
            return cls(max_concurrent_tasks=0)
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV} must be a non-negative integer, got {raw!r}"
            ) from exc
        if value < 0:
            raise ValueError(
                f"{BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV} must be >= 0 (0 = unlimited), got {value}"
            )
        return cls(max_concurrent_tasks=value)

    @classmethod
    def resolve(cls, *, max_concurrent_tasks: int | None = None) -> SchedulerRuntimeConfig:
        """CLI flag wins when set; otherwise read ``BLINK_SCHEDULER_MAX_CONCURRENT_TASKS``."""
        if max_concurrent_tasks is not None:
            if max_concurrent_tasks < 0:
                raise ValueError("max_concurrent_tasks must be >= 0 (0 = unlimited)")
            return cls(max_concurrent_tasks=max_concurrent_tasks)
        return cls.from_env()
