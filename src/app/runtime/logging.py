"""Loguru setup for console and file sinks."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from rich.console import Console


def configure_logging(
    log_file: Path,
    console: Console,
    debug: bool = False,
) -> None:
    """Configure global loguru sinks for this command run."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.configure(extra={"event": "app.log", "run_id": "-"})

    console_level = "DEBUG" if debug else "INFO"
    file_level = "DEBUG"

    logger.add(
        lambda message: console.print(message, end=""),
        level=console_level,
        colorize=False,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level:<8}</level> "
            "<cyan>{extra[event]}</cyan> "
            "<white>{message}</white>"
        ),
    )
    logger.add(
        log_file,
        level=file_level,
        enqueue=False,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "event={extra[event]} | run_id={extra[run_id]} | {message}"
        ),
    )


def event_logger(event: str, run_id: int | str = "-"):
    """Return a logger pre-bound with event metadata."""
    return logger.bind(event=event, run_id=run_id)
