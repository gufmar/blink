from __future__ import annotations

from pathlib import Path

import pytest

from app.schedule.runner import ensure_job_under_jobs_root


def test_rejects_path_outside_jobs_root(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    outside = tmp_path / "evil.job.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not under jobs_root"):
        ensure_job_under_jobs_root(jobs_root, outside)
