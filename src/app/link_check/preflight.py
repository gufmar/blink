"""HEAD/GET preflight for external link classification (Content-Type, downloads)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class PreflightResult:
    status_code: int | None
    content_type: str | None
    content_disposition: str | None
    error_message: str | None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def normalize_content_type(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    primary = str(raw).split(";")[0].strip().lower()
    return primary or None


def path_matches_extension(url: str, extensions: list[str]) -> bool:
    path = urlsplit(url).path.lower()
    for ext in extensions:
        token = str(ext).strip().lower()
        if token and path.endswith(token):
            return True
    return False


def content_type_matches_prefixes(content_type: str | None, prefixes: list[str]) -> bool:
    if not content_type:
        return False
    ct = content_type.lower()
    for prefix in prefixes:
        p = str(prefix).strip().lower()
        if p and ct.startswith(p):
            return True
    return False


def is_probably_html(content_type: str | None) -> bool:
    if content_type is None:
        return True
    ct = content_type.lower()
    return ct.startswith("text/html") or ct.startswith("application/xhtml+xml")


def should_skip_playwright_for_asset(
    *,
    url: str,
    content_type: str | None,
    skip_content_types: list[str],
    skip_extensions: list[str],
) -> bool:
    if content_type_matches_prefixes(content_type, skip_content_types):
        return True
    return path_matches_extension(url, skip_extensions)


def _headers_dict(msg: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in msg.items()}


def run_preflight(
    url: str,
    *,
    user_agent: str,
    timeout_seconds: int,
    follow_redirects: bool,
    max_get_body_bytes: int = 8192,
) -> PreflightResult:
    """Try HEAD, then GET with a small body read if HEAD is not allowed."""
    handlers: list[Any] = [] if follow_redirects else [_NoRedirectHandler()]
    opener = build_opener(*handlers)
    timeout = float(timeout_seconds)

    def head_request() -> PreflightResult | None:
        try:
            req = Request(url, method="HEAD", headers={"User-Agent": user_agent})
            with opener.open(req, timeout=timeout) as resp:
                code = int(resp.getcode())
                hdrs = _headers_dict(resp.headers)
                return PreflightResult(
                    status_code=code,
                    content_type=normalize_content_type(hdrs.get("content-type")),
                    content_disposition=hdrs.get("content-disposition"),
                    error_message=None,
                )
        except HTTPError as exc:
            code = int(exc.code)
            hdrs_obj = getattr(exc, "headers", None)
            hdrs = _headers_dict(hdrs_obj) if hdrs_obj is not None else {}
            if code in {405, 501}:
                return None
            return PreflightResult(
                status_code=code,
                content_type=normalize_content_type(hdrs.get("content-type")),
                content_disposition=hdrs.get("content-disposition"),
                error_message=None,
            )
        except URLError as exc:
            return PreflightResult(None, None, None, str(exc))

    head_out = head_request()
    if head_out is not None:
        return head_out

    try:
        req = Request(url, method="GET", headers={"User-Agent": user_agent})
        with opener.open(req, timeout=timeout) as resp:
            code = int(resp.getcode())
            hdrs = _headers_dict(resp.headers)
            chunk = resp.read(max_get_body_bytes)
            _ = chunk
            return PreflightResult(
                status_code=code,
                content_type=normalize_content_type(hdrs.get("content-type")),
                content_disposition=hdrs.get("content-disposition"),
                error_message=None,
            )
    except HTTPError as exc:
        code = int(exc.code)
        hdrs_obj = getattr(exc, "headers", None)
        hdrs = _headers_dict(hdrs_obj) if hdrs_obj is not None else {}
        return PreflightResult(
            status_code=code,
            content_type=normalize_content_type(hdrs.get("content-type")),
            content_disposition=hdrs.get("content-disposition"),
            error_message=None,
        )
    except URLError as exc:
        return PreflightResult(None, None, None, str(exc))


def preflight_ok_meta(method: str) -> str:
    return json.dumps({"method": method, "stage": "preflight"}, sort_keys=True)
