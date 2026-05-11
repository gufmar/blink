from __future__ import annotations

from pathlib import Path

import pytest

from app.schedule.runner import build_argv, ensure_job_under_jobs_root


def test_build_argv_uses_check_for_link_check(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    job = jobs_root / "x.job.json"
    job.write_text("{}", encoding="utf-8")
    assert "check" in build_argv(job, "link_check")
    assert build_argv(job, "link_check")[3] == "check"
    assert build_argv(job, "crawl")[3] == "crawl"


def test_rejects_path_outside_jobs_root(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    outside = tmp_path / "evil.job.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not under jobs_root"):
        ensure_job_under_jobs_root(jobs_root, outside)
