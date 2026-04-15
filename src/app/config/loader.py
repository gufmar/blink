"""Load Blink job configs and merge defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.job_config import JobConfig


def project_root() -> Path:
    """Return repository root from package location."""
    return Path(__file__).resolve().parents[3]


def default_job_path() -> Path:
    """Return the default job template path."""
    return project_root() / "jobs" / "_default.job.json"


def resolve_job_path(job: str | Path, cwd: Path | None = None) -> Path:
    """Resolve a user-provided job path to an absolute file path."""
    cwd = cwd or Path.cwd()
    candidate = Path(job)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve()


def load_json_file(path: Path) -> dict[str, Any]:
    """Load a JSON file and ensure it contains an object."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Expected a file path: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge two JSON objects.

    Dict values merge recursively.
    List and scalar values are replaced by override values.
    """
    merged: dict[str, Any] = dict(defaults)
    for key, override_value in overrides.items():
        default_value = merged.get(key)
        if isinstance(default_value, dict) and isinstance(override_value, dict):
            merged[key] = deep_merge(default_value, override_value)
        else:
            merged[key] = override_value
    return merged


def load_effective_job_config(job: str | Path, cwd: Path | None = None) -> JobConfig:
    """Load default+job config and return merged object."""
    job_path = resolve_job_path(job=job, cwd=cwd)
    defaults = load_json_file(default_job_path())
    job_overrides = load_json_file(job_path)
    merged = deep_merge(defaults, job_overrides)
    return merged  # type: ignore[return-value]
