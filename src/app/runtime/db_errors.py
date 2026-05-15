"""User-facing handling for SQLite failures in CLI commands."""

from __future__ import annotations

import sqlite3
import traceback

import typer
from loguru import logger

from app.persistence.sqlite_retry import is_sqlite_locked_error


def format_sqlite_failure(exc: BaseException, *, context: str) -> str:
    """Build a concise message including where the error surfaced."""
    exc_type = type(exc).__name__
    exc_msg = str(exc).strip() or "(no message)"
    where = traceback.format_exc().strip().splitlines()
    tail = "\n  ".join(where[-6:]) if where else "(no traceback)"
    hint = ""
    if is_sqlite_locked_error(exc):
        hint = (
            " The database was busy (another process or the web UI may be reading/writing the same "
            "job DB). Retries are applied automatically; if this persists, avoid heavy dashboard "
            "browsing during crawl/link-check runs or set BLINK_SCHEDULER_MAX_CONCURRENT_TASKS=1."
        )
    return f"{context} failed with {exc_type}: {exc_msg}.{hint}\nTraceback (tail):\n  {tail}"


def log_sqlite_failure(exc: BaseException, *, context: str, job_id: str, db_path: str) -> None:
    logger.exception(
        "{} job_id={} db={}: {}",
        context,
        job_id,
        db_path,
        exc,
    )


def exit_on_sqlite_failure(
    exc: BaseException,
    *,
    context: str,
    job_id: str,
    db_path: str,
) -> None:
    """Log full traceback to the job log file and exit with code 1."""
    log_sqlite_failure(exc, context=context, job_id=job_id, db_path=db_path)
    typer.secho(format_sqlite_failure(exc, context=context), fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1) from exc
