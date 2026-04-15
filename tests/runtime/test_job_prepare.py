from __future__ import annotations

from app.persistence.sqlite import connect_sqlite
from app.runtime.job_paths import build_job_paths
from app.runtime.job_prepare import prepare_job_runtime


def test_prepare_job_runtime_creates_missing_db(tmp_path) -> None:
    paths = build_job_paths("demo.job", root_dir=tmp_path)
    assert not paths.db_path.exists()
    notes = prepare_job_runtime(paths)
    assert paths.db_path.exists()
    assert paths.db_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.artifacts_dir.is_dir()
    assert paths.reports_dir.is_dir()
    assert notes
    conn = connect_sqlite(paths.db_path)
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM crawl_runs").fetchone()["c"]
        assert n == 0
    finally:
        conn.close()
