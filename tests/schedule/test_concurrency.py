from __future__ import annotations

import threading
import time

from app.schedule.concurrency import ScheduledTaskConcurrencyGate


def test_unlimited_runs_inline() -> None:
    gate = ScheduledTaskConcurrencyGate(0)
    seen: list[int] = []

    gate.run("j1", "crawl", lambda: seen.append(1))
    gate.run("j2", "crawl", lambda: seen.append(2))

    assert seen == [1, 2]
    assert gate.queued_count == 0


def test_limit_queues_until_slot_frees() -> None:
    gate = ScheduledTaskConcurrencyGate(1)
    order: list[str] = []
    release_first = threading.Event()

    def first() -> None:
        order.append("first_start")
        assert release_first.wait(timeout=2.0)
        order.append("first_end")

    def second() -> None:
        order.append("second")

    gate.run("j1", "crawl", first)
    deadline = time.time() + 2.0
    while "first_start" not in order and time.time() < deadline:
        time.sleep(0.01)

    gate.run("j2", "crawl", second)
    assert gate.queued_count == 1
    assert order == ["first_start"]

    release_first.set()
    deadline = time.time() + 2.0
    while "second" not in order and time.time() < deadline:
        time.sleep(0.01)

    assert order == ["first_start", "first_end", "second"]
    assert gate.queued_count == 0
