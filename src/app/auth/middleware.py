"""Session gate for dashboard and API routes."""

from __future__ import annotations

import html
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.auth.config import AuthConfig


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def _is_public_path(path: str) -> bool:
    p = _normalize_path(path)
    if p == "/health" or p.startswith("/notifications/slack"):
        return True
    if p.startswith("/static"):
        return True
    if p.startswith("/auth"):
        return True
    return False


def _login_redirect_url(request: Request) -> str:
    cfg: AuthConfig = request.app.state.auth_config
    base = _normalize_base_path(getattr(request.app.state, "route_base_path", ""))
    root = _normalize_base_path(str(request.scope.get("root_path") or ""))
    login_path = "/auth/login"
    if base and root.startswith(base):
        prefix = root
    else:
        parts = [base, root]
        prefix = "/".join(x.strip("/") for x in parts if x.strip("/"))
    if prefix:
        login_path = f"{prefix}{login_path}"
    nxt = request.url.path
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    return f"{login_path}?next={quote(nxt, safe='')}"


def _normalize_base_path(value: str | None) -> str:
    if not value:
        return ""
    trimmed = value.strip().strip("/")
    return f"/{trimmed}" if trimmed else ""


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        cfg: AuthConfig = request.app.state.auth_config
        if not cfg.any_enabled:
            return await call_next(request)

        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        if path.startswith("/dashboard") or path.startswith("/api/"):
            user_id = request.session.get("user_id")
            if user_id is None:
                accept = request.headers.get("accept", "")
                if path.startswith("/api/") or "application/json" in accept:
                    return JSONResponse({"ok": False, "error": "authentication_required"}, status_code=401)
                return RedirectResponse(_login_redirect_url(request), status_code=302)

        return await call_next(request)


def render_auth_error_html(title: str, message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>{html.escape(title)}</title></head>
<body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p><a href="/auth/login">Back to login</a></p></body></html>"""
