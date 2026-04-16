from __future__ import annotations

import hashlib
import hmac
import time

from app.notifications.slack.signature import verify_slack_signing_secret


def test_verify_slack_signing_secret_accepts_valid_request() -> None:
    secret = "shhh"
    body = b'{"type":"url_verification","challenge":"x"}'
    ts = str(int(time.time()))
    basestring = f"v0:{ts}:{body.decode('utf-8')}"
    digest = hmac.new(secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256).hexdigest()
    sig = f"v0={digest}"
    assert verify_slack_signing_secret(
        signing_secret=secret,
        request_timestamp=ts,
        raw_body=body,
        signature_header=sig,
    )


def test_verify_slack_signing_secret_rejects_wrong_sig() -> None:
    body = b"{}"
    ts = str(int(time.time()))
    assert not verify_slack_signing_secret(
        signing_secret="a",
        request_timestamp=ts,
        raw_body=body,
        signature_header="v0=wrong",
    )


def test_verify_slack_signing_secret_rejects_stale_timestamp() -> None:
    secret = "s"
    body = b"{}"
    old_ts = str(int(time.time()) - 3600)
    basestring = f"v0:{old_ts}:{body.decode('utf-8')}"
    digest = hmac.new(secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256).hexdigest()
    sig = f"v0={digest}"
    assert not verify_slack_signing_secret(
        signing_secret=secret,
        request_timestamp=old_ts,
        raw_body=body,
        signature_header=sig,
        max_age_seconds=300,
    )
