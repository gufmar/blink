"""Simple in-memory rate limiter for login attempts (per-process)."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class LoginRateLimiter:
    max_attempts: int
    window_seconds: int
    _buckets: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        arr = self._buckets[key]
        arr[:] = [t for t in arr if t >= cutoff]
        return len(arr) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        self._buckets[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._buckets.pop(key, None)
