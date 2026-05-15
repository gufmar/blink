"""Global SQLite database for Blink server auth (per jobs_root)."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def server_db_path(jobs_root: Path) -> Path:
    blink_dir = jobs_root.resolve() / ".blink"
    return blink_dir / "server.sqlite"


def connect_server_db(jobs_root: Path) -> sqlite3.Connection:
    blink_dir = jobs_root.resolve() / ".blink"
    blink_dir.mkdir(parents=True, exist_ok=True)
    db_path = server_db_path(jobs_root)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    initialize_server_schema(conn)
    return conn


def initialize_server_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT,
            google_sub TEXT UNIQUE,
            slack_user_id TEXT,
            is_global_admin INTEGER NOT NULL DEFAULT 0,
            disabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub);

        CREATE TABLE IF NOT EXISTS user_job_roles (
            user_id INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('watcher','solver','job_admin')),
            PRIMARY KEY (user_id, job_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_user_job_roles_job ON user_job_roles(job_id);

        CREATE TABLE IF NOT EXISTS auth_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            purpose TEXT NOT NULL CHECK(purpose IN ('password_setup','password_reset')),
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash);
        """
    )
    connection.commit()
