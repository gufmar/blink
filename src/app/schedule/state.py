"""Persistent scheduler task state under jobs_root/.blink/scheduler.sqlite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


def scheduler_db_path(jobs_root: Path) -> Path:
    """Return path to the shared scheduler SQLite file."""
    blink_dir = jobs_root.resolve() / ".blink"
    return blink_dir / "scheduler.sqlite"


def connect_state(jobs_root: Path) -> sqlite3.Connection:
    """Open scheduler state database, creating schema as needed."""
    blink_dir = jobs_root.resolve() / ".blink"
    blink_dir.mkdir(parents=True, exist_ok=True)
    db_path = blink_dir / "scheduler.sqlite"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    initialize_state_schema(conn)
    return conn


def initialize_state_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS scheduler_task_state (
            job_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            running INTEGER NOT NULL DEFAULT 0,
            last_start_at TEXT,
            last_end_at TEXT,
            last_exit_code INTEGER,
            last_error TEXT,
            pid INTEGER,
            PRIMARY KEY (job_id, task_type)
        );
        """
    )
    connection.commit()


@dataclass(frozen=True)
class TaskStateRow:
    job_id: str
    task_type: str
    running: bool
    last_start_at: str | None
    last_end_at: str | None
    last_exit_code: int | None
    last_error: str | None
    pid: int | None


class SchedulerStateStore:
    """Read/write rows in ``scheduler_task_state``."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root.resolve()

    def _conn(self) -> sqlite3.Connection:
        return connect_state(self._jobs_root)

    def get(self, job_id: str, task_type: str) -> TaskStateRow | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT job_id, task_type, running, last_start_at, last_end_at,
                       last_exit_code, last_error, pid
                FROM scheduler_task_state
                WHERE job_id = ? AND task_type = ?
                """,
                (job_id, task_type),
            ).fetchone()
        if row is None:
            return None
        return TaskStateRow(
            job_id=str(row["job_id"]),
            task_type=str(row["task_type"]),
            running=bool(row["running"]),
            last_start_at=row["last_start_at"],
            last_end_at=row["last_end_at"],
            last_exit_code=int(row["last_exit_code"]) if row["last_exit_code"] is not None else None,
            last_error=row["last_error"],
            pid=int(row["pid"]) if row["pid"] is not None else None,
        )

    def set_running(self, job_id: str, task_type: str, *, pid: int | None, start_iso: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_task_state(
                    job_id, task_type, running, last_start_at, pid
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(job_id, task_type) DO UPDATE SET
                    running = 1,
                    last_start_at = excluded.last_start_at,
                    pid = excluded.pid
                """,
                (job_id, task_type, start_iso, pid),
            )
            conn.commit()

    def set_finished(
        self,
        job_id: str,
        task_type: str,
        *,
        end_iso: str,
        exit_code: int,
        error: str | None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_task_state(
                    job_id, task_type, running, last_end_at, last_exit_code, last_error, pid
                ) VALUES (?, ?, 0, ?, ?, ?, NULL)
                ON CONFLICT(job_id, task_type) DO UPDATE SET
                    running = 0,
                    last_end_at = excluded.last_end_at,
                    last_exit_code = excluded.last_exit_code,
                    last_error = excluded.last_error,
                    pid = NULL
                """,
                (job_id, task_type, end_iso, exit_code, error),
            )
            conn.commit()

    def clear_running_if_stale(self, job_id: str, task_type: str) -> None:
        """Best-effort: clear running flag (e.g. after restart)."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE scheduler_task_state
                SET running = 0, pid = NULL
                WHERE job_id = ? AND task_type = ?
                """,
                (job_id, task_type),
            )
            conn.commit()
