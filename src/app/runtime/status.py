"""Rich live status line management."""

from __future__ import annotations

from rich.console import Console
from rich.live import Live
from rich.text import Text


class LiveStatus:
    """Keep a sticky bottom-line status while logs scroll above."""

    def __init__(self, enabled: bool = True) -> None:
        self.console = Console()
        self._enabled = enabled
        self._live: Live | None = None
        self._current = Text("Idle")

    def __enter__(self) -> "LiveStatus":
        if self._enabled:
            self._live = Live(self._current, console=self.console, refresh_per_second=8)
            self._live.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._live:
            self._live.stop()
            self._live = None

    def update(self, message: str) -> None:
        self._current = Text(message)
        if self._live:
            self._live.update(self._current)
