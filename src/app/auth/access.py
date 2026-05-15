"""Request-scoped job access checks for handlers."""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from app.auth.middleware import render_auth_error_html
from app.auth.permissions import EffectiveAccess, load_effective_access
from app.server.job_catalog import list_disk_job_ids


def _all_job_ids(jobs_root: Path) -> frozenset[str]:
    return list_disk_job_ids(jobs_root)


def require_job_access(
    request: Request,
    job_id: str,
    *,
    min_role: str = "watcher",
) -> Response | None:
    """Return error Response if denied; None if allowed. min_role reserved for future write ops."""
    _ = min_role
    jobs_root: Path = request.app.state.jobs_root
    all_ids = _all_job_ids(jobs_root)
    if job_id not in all_ids:
        return None
    access = load_effective_access(request, all_disk_job_ids=all_ids)
    if access is None:
        return None
    if access.can_read_job(job_id, all_ids):
        return None
    accept = request.headers.get("accept", "")
    if request.url.path.startswith("/api/") or "application/json" in accept:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return HTMLResponse(render_auth_error_html("Forbidden", "You do not have access to this job."), status_code=403)


def get_access_or_none(request: Request) -> EffectiveAccess | None:
    jobs_root: Path = request.app.state.jobs_root
    return load_effective_access(request, all_disk_job_ids=_all_job_ids(jobs_root))
