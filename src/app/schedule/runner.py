"""Invoke ``blink crawl`` / ``blink check`` via subprocess for scheduled runs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

TaskKind = Literal["crawl", "link_check"]


def ensure_job_under_jobs_root(jobs_root: Path, job_path: Path) -> Path:
    """Resolve ``job_path`` and verify it lies under ``jobs_root``."""
    root = jobs_root.resolve()
    resolved = job_path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"job path not under jobs_root: {resolved} ({root})") from exc
    return resolved


def build_argv(job_path: Path, task_kind: TaskKind) -> list[str]:
    """Build argv for ``python -m app.cli.main …``."""
    cmd = "check" if task_kind == "link_check" else "crawl"
    return [
        sys.executable,
        "-m",
        "app.cli.main",
        cmd,
        "run",
        "--job",
        str(job_path),
    ]


def run_scheduled_task(
    jobs_root: Path,
    job_path: Path,
    task_kind: TaskKind,
    *,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run crawl or link-check for ``job_path``; enforce ``jobs_root`` containment."""
    safe_path = ensure_job_under_jobs_root(jobs_root, job_path)
    argv = build_argv(safe_path, task_kind)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
