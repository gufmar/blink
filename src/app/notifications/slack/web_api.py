"""Minimal Slack Web API client (urllib, no extra deps)."""

from __future__ import annotations

import json
from typing import Any
from urllib import request
from urllib.error import URLError


def slack_web_api_call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to https://slack.com/api/{method}; return parsed JSON."""
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"https://slack.com/api/{method}",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except URLError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_json_response"}
    return data if isinstance(data, dict) else {"ok": False, "error": "invalid_response_shape"}


def chat_post_message(
    *,
    token: str,
    channel: str,
    text: str,
    thread_ts: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Post a channel message or thread reply. Returns (ok, ts, error)."""
    payload: dict[str, Any] = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    data = slack_web_api_call(token, "chat.postMessage", payload)
    if not data.get("ok"):
        err = str(data.get("error") or data.get("warning") or "unknown_error")
        return False, None, err
    ts = data.get("ts")
    return True, str(ts) if ts is not None else None, None
