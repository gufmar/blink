"""Session gate for dashboard and API routes."""

from __future__ import annotations

import html
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.auth.config import AuthConfig
from app.server.url_paths import external_path


def _is_public_path(path: str) -> bool:
    if path == "/health" or path.startswith("/notifications/slack"):
        return True
    if path.startswith("/static"):
        return True
    if path.startswith("/auth"):
        return True
    return False


def _login_redirect_url(request: Request) -> str:
    login = external_path(request, "/auth/login")
    nxt = external_path(request, request.url.path)
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    return f"{login}?next={quote(nxt, safe='')}"


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
