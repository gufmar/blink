"""Verify Slack Events API request signatures (signing secret)."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Final

_DEFAULT_MAX_AGE_SECONDS: Final[int] = 60 * 5


def verify_slack_signing_secret(
    *,
    signing_secret: str,
    request_timestamp: str,
    raw_body: bytes,
    signature_header: str | None,
    max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
) -> bool:
    """
    Verify ``X-Slack-Signature`` against ``X-Slack-Request-Timestamp`` and raw body.

    See https://api.slack.com/authentication/verifying-requests-from-slack
    """
    if not signing_secret or not signature_header:
        return False
    try:
        ts_int = int(request_timestamp)
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if abs(now - ts_int) > max_age_seconds:
        return False
    sig_basestring = f"v0:{request_timestamp}:{raw_body.decode('utf-8')}"
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature_header)
