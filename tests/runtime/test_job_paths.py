from __future__ import annotations

from datetime import date

from app.runtime.job_paths import build_job_paths, ensure_job_dirs


def test_build_job_paths_and_ensure_dirs(tmp_path) -> None:
    paths = build_job_paths("cardano.org", root_dir=tmp_path, on_date=date(2026, 4, 13))
    assert paths.db_path == tmp_path / "jobs" / "data" / "cardano.org" / "db" / "cardano.org.sqlite3"
    assert paths.log_path == tmp_path / "jobs" / "data" / "cardano.org" / "logs" / "2026-04-13.log"
    assert paths.artifacts_dir == tmp_path / "jobs" / "data" / "cardano.org" / "artifacts"
    assert paths.reports_dir == tmp_path / "jobs" / "data" / "cardano.org" / "reports"

    ensure_job_dirs(paths)
    assert paths.db_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.artifacts_dir.is_dir()
    assert paths.reports_dir.is_dir()
