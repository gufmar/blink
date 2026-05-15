from __future__ import annotations

import pytest

from app.schedule.runtime_config import (
    BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV,
    SchedulerRuntimeConfig,
)


def test_from_env_unset_is_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV, raising=False)
    assert SchedulerRuntimeConfig.from_env().max_concurrent_tasks == 0


def test_from_env_reads_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV, "2")
    assert SchedulerRuntimeConfig.from_env().max_concurrent_tasks == 2


def test_resolve_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BLINK_SCHEDULER_MAX_CONCURRENT_TASKS_ENV, "3")
    cfg = SchedulerRuntimeConfig.resolve(max_concurrent_tasks=1)
    assert cfg.max_concurrent_tasks == 1
