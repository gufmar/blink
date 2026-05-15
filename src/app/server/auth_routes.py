"""HTTP routes for login, logout, password setup, and Google OIDC."""

from __future__ import annotations

import html
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.auth.config import AuthConfig
from app.server.url_paths import absolute_public_url, cli_public_link, external_path
from app.auth.mailer import send_email, smtp_configured
from app.auth.passwords import hash_password, verify_password
from app.auth.rate_limit import LoginRateLimiter
from app.auth.repository import AuthRepository
from app.auth.tokens import generate_raw_token, hash_token, is_expired, token_expiry_iso
from app.server.dashboard_page import esc as html_esc
from app.server.dashboard_page import render_auth_page
from app.server.global_auth_db import connect_server_db

_MIN_PASSWORD_LEN = 10


def _esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _branding_links(request: Request) -> dict[str, str]:
    from app.server.asgi import _branding_links

    return _branding_links(request)


def _validate_password_pair(password: str, password_confirm: str) -> str | None:
    if len(password) < _MIN_PASSWORD_LEN:
        return f"Password must be at least {_MIN_PASSWORD_LEN} characters."
    if password != password_confirm:
        return "Passwords do not match."
    return None


def _invalid_token_html(request: Request) -> str:
    return render_auth_page(
        title="Blink · Invalid link",
        heading="Link not valid",
        subtitle="This setup or reset link cannot be used.",
        panel_title="What to do",
        panel_body_html=f'<div class="auth-plain-error">{html_esc(_invalid_token_message())}</div>',
        branding_links=_branding_links(request),
        nav_links=[("Sign in", _abs_url(request, "/auth/login"))],
    )


def _render_set_password_form(request: Request, token: str, *, error: str | None = None) -> str:
    action = _abs_url(request, "/auth/set-password")
    err_html = f'<p class="auth-err">{html_esc(error)}</p>' if error else ""
    form_body = f"""
<div class="auth-form">
  <p class="auth-notice"><strong>One-time link.</strong> This URL and token work only once. After you save a password, the link cannot be used again. The link expires after 72 hours.</p>
  {err_html}
  <form method="post" action="{html_esc(action)}">
    <input type="hidden" name="token" value="{html_esc(token)}"/>
    <label for="password">New password</label>
    <input id="password" type="password" name="password" required minlength="{_MIN_PASSWORD_LEN}" autocomplete="new-password"/>
    <label for="password_confirm">Confirm password</label>
    <input id="password_confirm" type="password" name="password_confirm" required minlength="{_MIN_PASSWORD_LEN}" autocomplete="new-password"/>
    <button type="submit">Save password</button>
  </form>
</div>"""
    return render_auth_page(
        title="Blink · Set password",
        heading="Set your password",
        subtitle="Choose a password for your Blink account.",
        panel_title="Account setup",
        panel_body_html=form_body,
        branding_links=_branding_links(request),
        nav_links=[("Sign in", _abs_url(request, "/auth/login"))],
    )


def _invalid_token_message() -> str:
    return (
        "Invalid or expired link.\n\n"
        "Common causes:\n"
        "• The link was already used or is older than 72 hours.\n"
        "• blink user was run with a different BLINK_SESSION_SECRET than blink serve "
        "(compare: blink user check --env-file /etc/blink/blink-serve.env).\n"
        "• blink user used a different --jobs-root than the running server.\n"
        "• The token was truncated when copied (paste the full URL from the CLI)."
    )


def _abs_url(request: Request, path: str) -> str:
    cfg: AuthConfig = request.app.state.auth_config
    return absolute_public_url(request, path, public_base_url=cfg.public_base_url)


def _repo(request: Request) -> AuthRepository:
    jobs_root: Path = request.app.state.jobs_root
    conn = connect_server_db(jobs_root)
    request.state.auth_db_conn = conn
    return AuthRepository(conn)


def _close_auth_conn(request: Request) -> None:
    conn = getattr(request.state, "auth_db_conn", None)
    if conn is not None:
        conn.close()


def _rate_limiter(request: Request) -> LoginRateLimiter:
    return request.app.state.login_rate_limiter


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


async def login_page(request: Request) -> HTMLResponse:
    cfg: AuthConfig = request.app.state.auth_config
    err = request.query_params.get("error", "")
    msg = ""
    if err == "invalid":
        msg = '<p class="err">Invalid email or password.</p>'
    elif err == "disabled":
        msg = '<p class="err">Account disabled.</p>'
    elif err == "rate":
        msg = '<p class="err">Too many attempts. Try again later.</p>'
    google_btn = ""
    if cfg.google_enabled:
        google_btn = f'<p><a class="btn" href="{_esc(_abs_url(request, "/auth/google/start"))}">Sign in with Google</a></p>'
    pwd_form = ""
    if cfg.password_enabled:
        default_next = external_path(request, "/dashboard")
        nxt = _esc(request.query_params.get("next", default_next))
        pwd_form = f"""
<form method="post" action="{_esc(_abs_url(request, "/auth/login"))}">
  <input type="hidden" name="next" value="{nxt}"/>
  <label>Email <input type="email" name="email" required autocomplete="username"/></label>
  <label>Password <input type="password" name="password" required autocomplete="current-password"/></label>
  <button type="submit">Sign in</button>
</form>"""
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Blink login</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 28rem; margin: 3rem auto; padding: 0 1rem; }}
label {{ display: block; margin: 0.75rem 0; }}
input {{ width: 100%; padding: 0.4rem; box-sizing: border-box; }}
button, .btn {{ margin-top: 1rem; padding: 0.5rem 1rem; }}
.err {{ color: #b91c1c; }}
</style></head>
<body><h1>Blink login</h1>{msg}{pwd_form}{google_btn}</body></html>"""
    return HTMLResponse(body)


async def login_post(request: Request) -> Response:
    cfg: AuthConfig = request.app.state.auth_config
    if not cfg.password_enabled:
        return RedirectResponse(_abs_url(request, "/auth/login"), status_code=302)
    form = await request.form()
    email = str(form.get("email") or "").strip().lower()
    password = str(form.get("password") or "")
    nxt = str(form.get("next") or external_path(request, "/dashboard"))
    ip = _client_ip(request)
    limiter = _rate_limiter(request)
    if limiter.is_blocked(ip):
        return RedirectResponse(_abs_url(request, "/auth/login?error=rate"), status_code=302)
    repo = _repo(request)
    try:
        user = repo.get_user_by_email(email)
        ok = (
            user is not None
            and not user.disabled
            and user.password_hash is not None
            and verify_password(password, user.password_hash)
        )
        if not ok:
            limiter.record_failure(ip)
            return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)
        if user.disabled:
            return RedirectResponse(_abs_url(request, "/auth/login?error=disabled"), status_code=302)
        limiter.reset(ip)
        request.session["user_id"] = user.id
        request.session["email"] = user.email
        if not nxt.startswith("/"):
            nxt = external_path(request, "/dashboard")
        return RedirectResponse(nxt, status_code=302)
    finally:
        _close_auth_conn(request)


async def logout_post(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(_abs_url(request, "/auth/login"), status_code=302)


async def logout_get(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(_abs_url(request, "/auth/login"), status_code=302)


async def set_password_page(request: Request) -> HTMLResponse:
    raw = str(request.query_params.get("token") or "").strip()
    if not raw:
        return HTMLResponse(
            render_auth_page(
                title="Blink · Missing token",
                heading="Missing token",
                subtitle="No setup token was provided.",
                panel_title="Error",
                panel_body_html='<div class="auth-plain-error">Open the full link from your invitation email or CLI output.</div>',
                branding_links=_branding_links(request),
                nav_links=[("Sign in", _abs_url(request, "/auth/login"))],
            ),
            status_code=400,
        )
    cfg: AuthConfig = request.app.state.auth_config
    secret = cfg.session_secret or ""
    th = hash_token(secret, raw)
    repo = _repo(request)
    try:
        row = repo.get_auth_token_row(th)
        if row is None or row["used_at"] is not None or is_expired(str(row["expires_at"])):
            return HTMLResponse(_invalid_token_html(request), status_code=400)
    finally:
        _close_auth_conn(request)
    return HTMLResponse(_render_set_password_form(request, raw))


async def set_password_post(request: Request) -> Response:
    cfg: AuthConfig = request.app.state.auth_config
    secret = cfg.session_secret or ""
    form = await request.form()
    raw = str(form.get("token") or "").strip()
    password = str(form.get("password") or "")
    password_confirm = str(form.get("password_confirm") or "")
    pair_err = _validate_password_pair(password, password_confirm)
    if pair_err:
        if not raw:
            return HTMLResponse(_invalid_token_html(request), status_code=400)
        th_check = hash_token(secret, raw)
        repo = _repo(request)
        try:
            row = repo.get_auth_token_row(th_check)
            if row is None or row["used_at"] is not None or is_expired(str(row["expires_at"])):
                return HTMLResponse(_invalid_token_html(request), status_code=400)
        finally:
            _close_auth_conn(request)
        return HTMLResponse(_render_set_password_form(request, raw, error=pair_err), status_code=400)
    th = hash_token(secret, raw)
    repo = _repo(request)
    try:
        row = repo.get_auth_token_row(th)
        if row is None or row["used_at"] is not None or is_expired(str(row["expires_at"])):
            return HTMLResponse(_invalid_token_html(request), status_code=400)
        user_id = int(row["user_id"])
        consumed = repo.consume_auth_token(th)
        if consumed is None:
            return HTMLResponse(_invalid_token_html(request), status_code=400)
        repo.set_password_hash(user_id, hash_password(password))
        user = repo.get_user_by_id(user_id)
        if user:
            request.session["user_id"] = user.id
            request.session["email"] = user.email
        return RedirectResponse(external_path(request, "/dashboard"), status_code=302)
    finally:
        _close_auth_conn(request)


def _google_redirect_uri(request: Request) -> str:
    return _abs_url(request, "/auth/google/callback")


async def google_start(request: Request) -> Response:
    cfg: AuthConfig = request.app.state.auth_config
    if not cfg.google_enabled or not cfg.google_client_id:
        return HTMLResponse("Google sign-in is not enabled", status_code=404)
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    if cfg.google_allowed_hd:
        params["hd"] = cfg.google_allowed_hd
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url, status_code=302)


async def google_callback(request: Request) -> Response:
    cfg: AuthConfig = request.app.state.auth_config
    if not cfg.google_enabled:
        return HTMLResponse("Google sign-in is not enabled", status_code=404)
    err = request.query_params.get("error")
    if err:
        return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)
    state = request.query_params.get("state", "")
    if not state or state != request.session.pop("oauth_state", None):
        return HTMLResponse("Invalid OAuth state", status_code=400)
    code = request.query_params.get("code", "")
    if not code:
        return HTMLResponse("Missing code", status_code=400)

    token_url = "https://oauth2.googleapis.com/token"
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            token_url,
            data={
                "code": code,
                "client_id": cfg.google_client_id,
                "client_secret": cfg.google_client_secret,
                "redirect_uri": _google_redirect_uri(request),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)
        token_data = token_resp.json()
        id_token = token_data.get("id_token")
        if not id_token:
            return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)

        info_resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
        )
        if info_resp.status_code != 200:
            return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)
        claims: dict[str, Any] = info_resp.json()

    email = str(claims.get("email") or "").strip().lower()
    sub = str(claims.get("sub") or "")
    verified = claims.get("email_verified") in (True, "true", "True", 1)
    hd = str(claims.get("hd") or "")
    if not email or not sub or not verified:
        return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)
    if cfg.google_allowed_hd and hd != cfg.google_allowed_hd:
        return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)
    aud = str(claims.get("aud") or "")
    if cfg.google_client_id and aud != cfg.google_client_id:
        return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)

    repo = _repo(request)
    try:
        user = repo.get_user_by_google_sub(sub) or repo.get_user_by_email(email)
        if user is None:
            user_id = repo.create_user(email=email, google_sub=sub)
            user = repo.get_user_by_id(user_id)
        elif user is not None:
            if user.disabled:
                return RedirectResponse(_abs_url(request, "/auth/login?error=disabled"), status_code=302)
            if user.google_sub != sub:
                repo.set_google_sub(user.id, sub)
        if user is None:
            return RedirectResponse(_abs_url(request, "/auth/login?error=invalid"), status_code=302)
        request.session["user_id"] = user.id
        request.session["email"] = user.email
        return RedirectResponse(external_path(request, "/dashboard"), status_code=302)
    finally:
        _close_auth_conn(request)


def issue_password_token(
    repo: AuthRepository,
    *,
    user_id: int,
    purpose: str,
    session_secret: str,
    public_url: str,
    route_base_path: str = "",
) -> tuple[str, str]:
    raw = generate_raw_token()
    th = hash_token(session_secret, raw)
    repo.insert_auth_token(
        user_id=user_id,
        purpose=purpose,  # type: ignore[arg-type]
        token_hash=th,
        expires_at_iso=token_expiry_iso(hours=72),
    )
    path = f"/auth/set-password?token={quote(raw, safe='')}"
    link = cli_public_link(public_url, route_base_path, path)
    return raw, link


def maybe_send_setup_email(cfg: AuthConfig, *, to_email: str, link: str) -> bool:
    if not smtp_configured(cfg):
        return False
    send_email(
        cfg,
        to_addrs=[to_email],
        subject="Blink — set your password",
        body_text=f"Open this link to set your password (expires in 72 hours):\n\n{link}\n",
        body_html=f'<p>Open <a href="{html.escape(link)}">this link</a> to set your password (expires in 72 hours).</p>',
    )
    return True


def auth_route_handlers() -> list[tuple[str, Any, list[str], str]]:
    return [
        ("/auth/login", login_page, ["GET"], "auth_login"),
        ("/auth/login", login_post, ["POST"], "auth_login_post"),
        ("/auth/logout", logout_get, ["GET"], "auth_logout_get"),
        ("/auth/logout", logout_post, ["POST"], "auth_logout_post"),
        ("/auth/set-password", set_password_page, ["GET"], "auth_set_password"),
        ("/auth/set-password", set_password_post, ["POST"], "auth_set_password_post"),
        ("/auth/google/start", google_start, ["GET"], "auth_google_start"),
        ("/auth/google/callback", google_callback, ["GET"], "auth_google_callback"),
    ]
