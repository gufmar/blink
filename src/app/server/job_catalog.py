"""Canonical job identifiers from jobs_root (meta.job_id, not filename stems)."""

from __future__ import annotations

from pathlib import Path

from app.config.loader import load_effective_job_config
from app.config.schema import validate_job_config


def list_disk_job_ids(jobs_root: Path) -> frozenset[str]:
    """Job ids from valid ``*.job.json`` files (``meta.job_id``)."""
    ids: set[str] = set()
    for job_path in sorted(jobs_root.glob("*.job.json")):
        if job_path.name.startswith("_"):
            continue
        try:
            config = load_effective_job_config(job_path)
        except (FileNotFoundError, ValueError, OSError):
            continue
        issues = validate_job_config(config)
        if issues:
            continue
        job_id = str(config["meta"]["job_id"] or "").strip()
        if job_id:
            ids.add(job_id)
    return frozenset(ids)
