from __future__ import annotations

from app.runtime.formatting import format_bytes_human


def test_format_bytes_human() -> None:
    assert format_bytes_human(500).endswith(" B")
    assert "KB" in format_bytes_human(20480)
