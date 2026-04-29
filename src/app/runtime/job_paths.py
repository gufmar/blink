"""Per-job runtime path resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.config.loader import project_root


@dataclass(frozen=True)
class JobPaths:
    job_root: Path
    db_dir: Path
    db_path: Path
    logs_dir: Path
    log_path: Path
    artifacts_dir: Path
    reports_dir: Path


def build_job_paths(job_id: str, root_dir: Path | None = None, on_date: date | None = None) -> JobPaths:
    """Return canonical runtime paths for one job id."""
    root = root_dir or project_root()
    day = on_date or date.today()
    job_root = root / "jobs" / "data" / job_id
    db_dir = job_root / "db"
    logs_dir = job_root / "logs"
    artifacts_dir = job_root / "artifacts"
    reports_dir = job_root / "reports"
    db_path = db_dir / f"{job_id}.sqlite3"
    log_path = logs_dir / f"{day.isoformat()}.log"
    return JobPaths(
        job_root=job_root,
        db_dir=db_dir,
        db_path=db_path,
        logs_dir=logs_dir,
        log_path=log_path,
        artifacts_dir=artifacts_dir,
        reports_dir=reports_dir,
    )


def ensure_job_dirs(paths: JobPaths) -> None:
    """Ensure all runtime directories exist for this job."""
    paths.db_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
