"""Global limit on concurrently running scheduled tasks (crawl + link-check)."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

from loguru import logger


@dataclass(frozen=True)
class _QueuedRun:
    job_id: str
    task_type: str
    runner: Callable[[], None]


class ScheduledTaskConcurrencyGate:
    """When ``max_concurrent`` > 0, run at most that many scheduled tasks at once."""

    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent < 0:
            raise ValueError("max_concurrent must be >= 0")
        self._max_concurrent = max_concurrent
        self._limit_enabled = max_concurrent > 0
        self._sem = threading.Semaphore(max_concurrent) if self._limit_enabled else None
        self._queue: deque[_QueuedRun] = deque()
        self._queue_lock = threading.Lock()
        self._queued_count = 0

    @property
    def max_concurrent_tasks(self) -> int:
        return self._max_concurrent

    @property
    def queued_count(self) -> int:
        with self._queue_lock:
            return self._queued_count

    def run(self, job_id: str, task_type: str, runner: Callable[[], None]) -> None:
        """Invoke ``runner`` immediately or enqueue when the global limit is reached."""
        if not self._limit_enabled:
            runner()
            return
        assert self._sem is not None
        if self._sem.acquire(blocking=False):
            self._spawn(job_id, task_type, runner)
            return
        with self._queue_lock:
            self._queue.append(_QueuedRun(job_id=job_id, task_type=task_type, runner=runner))
            self._queued_count += 1
            depth = self._queued_count
        logger.info(
            "Queued scheduled {} {} (global queue depth={}, max_concurrent={})",
            job_id,
            task_type,
            depth,
            self._max_concurrent,
        )

    def _spawn(self, job_id: str, task_type: str, runner: Callable[[], None]) -> None:
        def work() -> None:
            try:
                runner()
            finally:
                self._finish_slot()

        threading.Thread(
            target=work,
            name=f"blink-sched-{job_id}-{task_type}",
            daemon=True,
        ).start()

    def _finish_slot(self) -> None:
        assert self._sem is not None
        with self._queue_lock:
            if self._queue:
                item = self._queue.popleft()
                self._queued_count -= 1
                depth = self._queued_count
            else:
                item = None
        if item is None:
            self._sem.release()
            return
        logger.info(
            "Dequeuing scheduled {} {} (global queue depth={}, max_concurrent={})",
            item.job_id,
            item.task_type,
            depth,
            self._max_concurrent,
        )
        self._spawn(item.job_id, item.task_type, item.runner)
