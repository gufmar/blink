"""JSON schema validation for Blink jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from app.config.loader import project_root


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


def schema_path() -> Path:
    """Return the job schema file path."""
    return project_root() / "jobs" / "job.schema.v1.json"


def load_job_schema() -> dict[str, Any]:
    """Load and parse JSON schema."""
    path = schema_path()
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Schema must be a JSON object: {path}")
    return data


def validate_job_config(config: dict[str, Any]) -> list[ValidationIssue]:
    """Validate a config object and return all issues."""
    if config.get("job_version") != 1:
        return [
            ValidationIssue(
                path="job_version",
                message=f"Unsupported job_version {config.get('job_version')!r}; expected 1.",
            )
        ]

    validator = Draft202012Validator(load_job_schema(), format_checker=FormatChecker())
    issues: list[ValidationIssue] = []
    for error in sorted(validator.iter_errors(config), key=lambda err: list(err.path)):
        path = ".".join(str(part) for part in error.path) or "$"
        issues.append(ValidationIssue(path=path, message=error.message))
    return issues
