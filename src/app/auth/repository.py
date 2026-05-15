"""Persistence for users, roles, and auth tokens."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

JobRole = Literal["watcher", "solver", "job_admin"]
TokenPurpose = Literal["password_setup", "password_reset"]


@dataclass(frozen=True, slots=True)
class UserRow:
    id: int
    email: str
    password_hash: str | None
    google_sub: str | None
    slack_user_id: str | None
    is_global_admin: bool
    disabled: bool


class AuthRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def get_user_by_id(self, user_id: int) -> UserRow | None:
        row = self._conn.execute(
            """
            SELECT id, email, password_hash, google_sub, slack_user_id, is_global_admin, disabled
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> UserRow | None:
        row = self._conn.execute(
            """
            SELECT id, email, password_hash, google_sub, slack_user_id, is_global_admin, disabled
            FROM users WHERE email = ?
            """,
            (email.strip().lower(),),
        ).fetchone()
        return self._row_to_user(row)

    def get_user_by_google_sub(self, google_sub: str) -> UserRow | None:
        row = self._conn.execute(
            """
            SELECT id, email, password_hash, google_sub, slack_user_id, is_global_admin, disabled
            FROM users WHERE google_sub = ?
            """,
            (google_sub,),
        ).fetchone()
        return self._row_to_user(row)

    def list_users(self) -> list[UserRow]:
        rows = self._conn.execute(
            """
            SELECT id, email, password_hash, google_sub, slack_user_id, is_global_admin, disabled
            FROM users ORDER BY email
            """
        ).fetchall()
        return [u for r in rows if (u := self._row_to_user(r)) is not None]

    def create_user(
        self,
        *,
        email: str,
        password_hash: str | None = None,
        google_sub: str | None = None,
        slack_user_id: str | None = None,
        is_global_admin: bool = False,
    ) -> int:
        norm = email.strip().lower()
        cur = self._conn.execute(
            """
            INSERT INTO users(email, password_hash, google_sub, slack_user_id, is_global_admin)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                norm,
                password_hash,
                google_sub,
                slack_user_id,
                1 if is_global_admin else 0,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def delete_user_by_email(self, email: str) -> bool:
        norm = email.strip().lower()
        cur = self._conn.execute("DELETE FROM users WHERE email = ?", (norm,))
        self._conn.commit()
        return cur.rowcount > 0

    def set_password_hash(self, user_id: int, password_hash: str | None) -> None:
        self._conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE id = ?",
            (password_hash, user_id),
        )
        self._conn.commit()

    def set_google_sub(self, user_id: int, google_sub: str | None) -> None:
        self._conn.execute(
            "UPDATE users SET google_sub = ?, updated_at = datetime('now') WHERE id = ?",
            (google_sub, user_id),
        )
        self._conn.commit()

    def set_slack_user_id(self, user_id: int, slack_user_id: str | None) -> None:
        self._conn.execute(
            "UPDATE users SET slack_user_id = ?, updated_at = datetime('now') WHERE id = ?",
            (slack_user_id, user_id),
        )
        self._conn.commit()

    def set_global_admin(self, user_id: int, *, is_admin: bool) -> None:
        self._conn.execute(
            "UPDATE users SET is_global_admin = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if is_admin else 0, user_id),
        )
        self._conn.commit()

    def set_disabled(self, user_id: int, *, disabled: bool) -> None:
        self._conn.execute(
            "UPDATE users SET disabled = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if disabled else 0, user_id),
        )
        self._conn.commit()

    def set_job_role(self, user_id: int, job_id: str, role: JobRole) -> None:
        self._conn.execute(
            """
            INSERT INTO user_job_roles(user_id, job_id, role) VALUES (?, ?, ?)
            ON CONFLICT(user_id, job_id) DO UPDATE SET role = excluded.role
            """,
            (user_id, job_id.strip(), role),
        )
        self._conn.commit()

    def clear_job_role(self, user_id: int, job_id: str) -> None:
        self._conn.execute("DELETE FROM user_job_roles WHERE user_id = ? AND job_id = ?", (user_id, job_id.strip()))
        self._conn.commit()

    def list_job_roles(self, user_id: int) -> dict[str, JobRole]:
        rows = self._conn.execute(
            "SELECT job_id, role FROM user_job_roles WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {str(r["job_id"]): str(r["role"]) for r in rows}  # type: ignore[misc]

    def is_global_admin(self, user_id: int) -> bool:
        row = self._conn.execute("SELECT is_global_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row and row["is_global_admin"])

    def list_accessible_job_ids(self, user_id: int) -> frozenset[str]:
        rows = self._conn.execute(
            "SELECT job_id FROM user_job_roles WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return frozenset(str(r["job_id"]) for r in rows)

    def insert_auth_token(
        self,
        *,
        user_id: int,
        purpose: TokenPurpose,
        token_hash: str,
        expires_at_iso: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO auth_tokens(user_id, purpose, token_hash, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, purpose, token_hash, expires_at_iso),
        )
        self._conn.commit()

    def get_auth_token_row(self, token_hash: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT id, user_id, purpose, expires_at, used_at FROM auth_tokens
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    def consume_auth_token(self, token_hash: str) -> tuple[int, TokenPurpose] | None:
        row = self._conn.execute(
            """
            SELECT id, user_id, purpose, expires_at, used_at FROM auth_tokens
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None or row["used_at"] is not None:
            return None
        self._conn.execute(
            "UPDATE auth_tokens SET used_at = datetime('now') WHERE id = ?",
            (int(row["id"]),),
        )
        self._conn.commit()
        return (int(row["user_id"]), str(row["purpose"]))  # type: ignore[return-value]

    def _row_to_user(self, row: sqlite3.Row | None) -> UserRow | None:
        if row is None:
            return None
        return UserRow(
            id=int(row["id"]),
            email=str(row["email"]),
            password_hash=str(row["password_hash"]) if row["password_hash"] is not None else None,
            google_sub=str(row["google_sub"]) if row["google_sub"] is not None else None,
            slack_user_id=str(row["slack_user_id"]) if row["slack_user_id"] is not None else None,
            is_global_admin=bool(row["is_global_admin"]),
            disabled=bool(row["disabled"]),
        )
