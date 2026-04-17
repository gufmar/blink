"""Discover validated job configs under jobs_root for scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.config.loader import load_effective_job_config
from app.config.schema import validate_job_config
from app.models.job_config import JobConfig


@dataclass(frozen=True)
class ScheduleRegistryEntry:
    """One job file eligible for scheduling (may register crawl and/or link_check tasks)."""

    job_path: Path
    slug: str
    config: JobConfig

    @property
    def job_id(self) -> str:
        return self.config["meta"]["job_id"]


def iter_job_files(jobs_root: Path) -> list[Path]:
    """List ``*.job.json`` in ``jobs_root``, skipping underscore-prefixed templates."""
    root = jobs_root.resolve()
    paths = sorted(root.glob("*.job.json"))
    out: list[Path] = []
    for p in paths:
        if p.name.startswith("_"):
            continue
        out.append(p)
    return out


def load_registry(jobs_root: Path) -> list[ScheduleRegistryEntry]:
    """Load and validate all non-template job files; skip invalid entries."""
    entries: list[ScheduleRegistryEntry] = []
    for job_path in iter_job_files(jobs_root):
        try:
            config = load_effective_job_config(job_path)
        except (FileNotFoundError, ValueError, OSError) as exc:
            logger.warning("Skipping job file {}: {}", job_path, exc)
            continue
        issues = validate_job_config(config)
        if issues:
            logger.warning("Skipping invalid job {}: {}", job_path, issues[0].message)
            continue
        cfg = config  # type: ignore[assignment]
        if not cfg["meta"]["enabled"]:
            continue
        stem = job_path.stem  # e.g. cardano.org.job for *.job.json
        slug = stem.removesuffix(".job") if stem.endswith(".job") else stem
        entries.append(
            ScheduleRegistryEntry(
                job_path=job_path.resolve(),
                slug=slug,
                config=cfg,
            )
        )
    return entries
