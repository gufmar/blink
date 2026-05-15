"""RBAC helpers for HTTP handlers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from starlette.requests import Request

from app.auth.config import AuthConfig
from app.auth.repository import AuthRepository
from app.server.global_auth_db import connect_server_db


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    user_id: int
    email: str
    is_global_admin: bool
    job_ids: frozenset[str]

    def can_read_job(self, job_id: str, all_job_ids: frozenset[str]) -> bool:
        if self.is_global_admin:
            return job_id in all_job_ids
        return job_id in self.job_ids


def load_effective_access(request: Request, *, all_disk_job_ids: frozenset[str]) -> EffectiveAccess | None:
    """Return None when auth is disabled (caller treats as full access)."""
    cfg: AuthConfig = request.app.state.auth_config
    if not cfg.any_enabled:
        return None
    raw = request.session.get("user_id")
    if raw is None:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return None
    jobs_root: Path = request.app.state.jobs_root
    conn = connect_server_db(jobs_root)
    try:
        repo = AuthRepository(conn)
        user = repo.get_user_by_id(user_id)
        if user is None or user.disabled:
            return None
        if user.is_global_admin:
            return EffectiveAccess(
                user_id=user.id,
                email=user.email,
                is_global_admin=True,
                job_ids=all_disk_job_ids,
            )
        job_ids = repo.list_accessible_job_ids(user_id)
        allowed = job_ids & all_disk_job_ids
        return EffectiveAccess(user_id=user.id, email=user.email, is_global_admin=False, job_ids=allowed)
    finally:
        conn.close()


def filter_jobs_for_access(
    jobs: list[dict],
    access: EffectiveAccess | None,
    *,
    all_disk_job_ids: frozenset[str],
) -> list[dict]:
    if access is None:
        return jobs
    if access.is_global_admin:
        return [j for j in jobs if str(j.get("job_id")) in all_disk_job_ids]
    allowed = access.job_ids
    return [j for j in jobs if str(j.get("job_id")) in allowed]


def filter_schedule_tasks(tasks: list[dict], access: EffectiveAccess | None, *, all_disk_job_ids: frozenset[str]) -> list[dict]:
    if access is None:
        return tasks
    if access.is_global_admin:
        return [t for t in tasks if str(t.get("job_id") or "") in all_disk_job_ids]
    allowed = access.job_ids
    return [t for t in tasks if str(t.get("job_id") or "") in allowed]
