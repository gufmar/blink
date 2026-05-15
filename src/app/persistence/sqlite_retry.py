"""Retry helpers for transient SQLite lock errors."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

from loguru import logger

DEFAULT_LOCK_RETRY_ATTEMPTS = 4
DEFAULT_LOCK_RETRY_DELAY_SECONDS = 1.0

T = TypeVar("T")


def is_sqlite_locked_error(exc: BaseException) -> bool:
    """Return True when ``exc`` is a SQLite lock/busy contention error."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def run_with_sqlite_retry(
    action: Callable[[], T],
    *,
    attempts: int = DEFAULT_LOCK_RETRY_ATTEMPTS,
    delay_seconds: float = DEFAULT_LOCK_RETRY_DELAY_SECONDS,
    action_label: str = "sqlite",
) -> T:
    """Run ``action``; on lock errors sleep and retry up to ``attempts`` times."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except sqlite3.OperationalError as exc:
            if not is_sqlite_locked_error(exc):
                raise
            last_exc = exc
            if attempt >= attempts:
                break
            logger.warning(
                "SQLite {} locked (attempt {}/{}): {}; retrying in {}s",
                action_label,
                attempt,
                attempts,
                exc,
                delay_seconds,
            )
            time.sleep(delay_seconds)
    assert last_exc is not None
    raise last_exc


def commit_with_retry(
    connection: sqlite3.Connection,
    *,
    attempts: int = DEFAULT_LOCK_RETRY_ATTEMPTS,
    delay_seconds: float = DEFAULT_LOCK_RETRY_DELAY_SECONDS,
) -> None:
    """``connection.commit()`` with retries on transient lock errors."""
    run_with_sqlite_retry(
        connection.commit,
        attempts=attempts,
        delay_seconds=delay_seconds,
        action_label="commit",
    )


def configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    """Pragmas for safer concurrent access (scheduler, CLI, web UI)."""
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
