"""Load key=value environment files (e.g. systemd EnvironmentFile)."""

from __future__ import annotations

import os
import re
from pathlib import Path

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {"'", '"'}:
        return v[1:-1]
    return v


def load_env_file(path: Path, *, override: bool = True) -> int:
    """
    Load variables from a dotenv-style file into ``os.environ``.

    Returns the number of variables applied. When ``override`` is True (default),
    values from the file replace existing environment entries — use this for
    ``--env-file /etc/blink/blink-serve.env`` so CLI matches systemd.
    """
    text = path.read_text(encoding="utf-8")
    applied = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        key, raw_value = match.group(1), match.group(2)
        if "#" in raw_value and not raw_value.strip().startswith(('"', "'")):
            raw_value = raw_value.split("#", 1)[0]
        value = _strip_quotes(raw_value)
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied += 1
    return applied


def session_secret_fingerprint(secret: str | None) -> str:
    """Short stable fingerprint to compare CLI vs serve (not reversible)."""
    import hashlib

    digest = hashlib.sha256((secret or "").encode("utf-8")).hexdigest()
    return digest[:12] if secret else "<unset>"
