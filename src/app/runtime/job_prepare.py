"""Ensure per-job runtime folders and database exist before a job run."""

from __future__ import annotations

from pathlib import Path

from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.runtime.job_paths import JobPaths, ensure_job_dirs


def prepare_job_database(db_path: Path) -> list[str]:
    """Ensure job directories exist; if the SQLite file is missing, create an empty schema.

    Returns human-readable notes for logging (e.g. when the DB was recreated).
    """
    notes: list[str] = []
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        connection = connect_sqlite(db_path)
        try:
            initialize_schema(connection)
        finally:
            connection.close()
        notes.append(f"Initialized empty database at {db_path}")
    return notes


def prepare_job_runtime(paths: JobPaths) -> list[str]:
    """Ensure standard job layout (db/, logs/, artifacts/) and default DB file."""
    ensure_job_dirs(paths)
    return prepare_job_database(paths.db_path)
