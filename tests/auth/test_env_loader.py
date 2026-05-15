from __future__ import annotations

import os
from pathlib import Path

from app.auth.env_loader import load_env_file, session_secret_fingerprint


def test_load_env_file_overrides(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / "blink.env"
    env.write_text(
        "BLINK_SESSION_SECRET=from-file\nBLINK_PUBLIC_BASE_URL=https://example.org/blink\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BLINK_SESSION_SECRET", "from-shell")
    n = load_env_file(env, override=True)
    assert n == 2
    assert os.environ["BLINK_SESSION_SECRET"] == "from-file"
    assert session_secret_fingerprint("from-file") != session_secret_fingerprint("from-shell")
