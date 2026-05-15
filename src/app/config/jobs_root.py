"""Resolve the Blink jobs directory (CLI flags, env, defaults)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from app.config.loader import project_root

BLINK_JOBS_ROOT_ENV = "BLINK_JOBS_ROOT"

JobsRootOption = Annotated[
    Path | None,
    typer.Option(
        "--jobs-root",
        envvar=BLINK_JOBS_ROOT_ENV,
        help=(
            "Directory containing <slug>.job.json files. "
            f"Defaults to ${BLINK_JOBS_ROOT_ENV} or <project>/jobs."
        ),
    ),
]


def resolve_jobs_root(jobs_root: Path | None = None) -> Path:
    """
    Resolve jobs root: ``--jobs-root`` flag, then ``BLINK_JOBS_ROOT``, then ``<project>/jobs``.
    """
    if jobs_root is not None:
        return jobs_root.expanduser().resolve()
    env = os.getenv(BLINK_JOBS_ROOT_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (project_root() / "jobs").resolve()
