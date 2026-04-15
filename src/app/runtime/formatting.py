"""Human-readable formatting helpers."""

from __future__ import annotations


def format_bytes_human(num_bytes: int) -> str:
    """Format byte size for display, e.g. ``20,7 MB`` (comma as decimal separator)."""
    if num_bytes < 0:
        num_bytes = 0
    if num_bytes < 1024:
        return f"{num_bytes} B"
    value = float(num_bytes) / 1024.0
    for unit in ("KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            text = f"{value:.1f}".replace(".", ",")
            return f"{text} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"
