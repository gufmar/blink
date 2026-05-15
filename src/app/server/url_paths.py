"""Proxy-safe URL path helpers (shared by dashboard and auth)."""

from __future__ import annotations

from starlette.requests import Request


def normalize_base_path(value: str | None) -> str:
    if not value:
        return ""
    trimmed = value.strip().strip("/")
    return f"/{trimmed}" if trimmed else ""


def join_url_paths(*parts: str) -> str:
    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        chunk = str(part).strip("/")
        if chunk:
            cleaned.append(chunk)
    if not cleaned:
        return "/"
    return "/" + "/".join(cleaned)


def request_mount_prefix(request: Request) -> str:
    """External path prefix for this request (``--base-path`` + proxy ``root_path``, deduped)."""
    root_path = normalize_base_path(str(request.scope.get("root_path") or ""))
    config_base = normalize_base_path(getattr(request.app.state, "route_base_path", ""))
    if config_base and root_path.startswith(config_base):
        config_base = ""
    return join_url_paths(config_base, root_path)


def external_path(request: Request, app_path: str) -> str:
    """Browser path for an app route (e.g. ``/auth/login`` → ``/blink/auth/login``)."""
    rel = app_path if app_path.startswith("/") else f"/{app_path}"
    return join_url_paths(request_mount_prefix(request), rel)


def absolute_public_url(request: Request, app_path: str, *, public_base_url: str) -> str:
    """Full URL using ``BLINK_PUBLIC_BASE_URL`` (include mount path) or origin + deduped prefix."""
    public = public_base_url.rstrip("/")
    rel = app_path if app_path.startswith("/") else f"/{app_path}"
    prefix = request_mount_prefix(request)
    if prefix and prefix != "/" and public.endswith(prefix.rstrip("/")):
        return f"{public}{rel}"
    return f"{public}{external_path(request, app_path)}"


def cli_public_link(public_base_url: str, route_base_path: str, app_path: str) -> str:
    """Build setup/reset links from CLI (no HTTP request)."""
    public = public_base_url.rstrip("/")
    rel = app_path if app_path.startswith("/") else f"/{app_path}"
    base = normalize_base_path(route_base_path)
    if base and base != "/":
        if public.endswith(base.rstrip("/")):
            return f"{public}{rel}"
        return f"{public}{join_url_paths(base, rel)}"
    return f"{public}{rel}"
