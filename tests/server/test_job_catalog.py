from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.server.job_catalog import list_disk_job_ids


def test_list_disk_job_ids_uses_meta_job_id_not_filename(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    default_src = root / "jobs" / "_default.job.json"
    job_path = tmp_path / "alias.job.json"
    shutil.copy(default_src, job_path)
    data = json.loads(job_path.read_text(encoding="utf-8"))
    data["meta"]["job_id"] = "canonical.job.id"
    data["meta"]["enabled"] = True
    job_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    ids = list_disk_job_ids(tmp_path)
    assert ids == frozenset({"canonical.job.id"})
    assert "alias" not in ids
