"""Opaque URL tokens (setup / reset): store only HMAC digest."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta


def generate_raw_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(server_secret: str, raw: str) -> str:
    return hmac.new(server_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def token_expiry_iso(*, hours: int = 72) -> str:
    return (datetime.now(tz=UTC) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def is_expired(expires_at_iso: str) -> bool:
    try:
        raw = expires_at_iso.strip()
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        exp = datetime.fromisoformat(normalized)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
    except ValueError:
        return True
    return datetime.now(tz=UTC) >= exp
