"""Frontier queue and URL de-duplication."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class FrontierItem:
    url: str
    depth: int


class CrawlFrontier:
    """FIFO queue with seen/queued tracking."""

    def __init__(self) -> None:
        self._queue: deque[FrontierItem] = deque()
        self._queued: set[str] = set()
        self._visited: set[str] = set()

    def enqueue(self, url: str, depth: int) -> bool:
        if url in self._visited or url in self._queued:
            return False
        self._queue.append(FrontierItem(url=url, depth=depth))
        self._queued.add(url)
        return True

    def pop(self) -> FrontierItem | None:
        if not self._queue:
            return None
        item = self._queue.popleft()
        self._queued.discard(item.url)
        self._visited.add(item.url)
        return item

    def __len__(self) -> int:
        return len(self._queue)
