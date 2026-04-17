"""Parse schedule interval expressions (e.g. 24h, 30m)."""

from __future__ import annotations

import re
from datetime import timedelta

_INTERVAL_PARTS = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)


def parse_interval_expression(expr: str) -> timedelta:
    """Parse a duration string into a timedelta.

    Accepts concatenated parts such as ``24h``, ``6h30m``, ``15m``, ``45s``, ``1d``.
    Each part is ``<int><unit>`` where unit is d/h/m/s.
    """
    raw = expr.strip()
    if not raw:
        raise ValueError("empty interval expression")

    total = timedelta(0)
    for match in _INTERVAL_PARTS.finditer(raw):
        n = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "d":
            total += timedelta(days=n)
        elif unit == "h":
            total += timedelta(hours=n)
        elif unit == "m":
            total += timedelta(minutes=n)
        else:
            total += timedelta(seconds=n)

    if total <= timedelta(0):
        raise ValueError(f"invalid or empty interval expression: {expr!r}")

    # Reject if junk remains (strict)
    reconstructed = "".join(m.group(0).replace(" ", "") for m in _INTERVAL_PARTS.finditer(raw))
    compact = re.sub(r"\s+", "", raw)
    if compact.lower() != reconstructed.lower():
        raise ValueError(f"invalid interval expression: {expr!r}")

    return total
