"""SQLite schema/bootstrap utilities."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with row access by name."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create required crawl tables if they do not exist."""
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            pages_visited INTEGER NOT NULL DEFAULT 0,
            pages_failed INTEGER NOT NULL DEFAULT 0,
            links_discovered INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS crawl_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            depth INTEGER NOT NULL,
            status_code INTEGER,
            ok INTEGER NOT NULL,
            error_message TEXT,
            html TEXT,
            main_text TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_crawl_pages_url_created_at
            ON crawl_pages(url, created_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS crawl_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            target_url TEXT NOT NULL,
            is_internal INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_crawl_links_run_target
            ON crawl_links(run_id, target_url);

        CREATE TABLE IF NOT EXISTS link_check_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_link_id INTEGER NOT NULL,
            crawl_run_id INTEGER NOT NULL,
            target_url TEXT NOT NULL,
            status_code INTEGER,
            ok INTEGER NOT NULL,
            error_message TEXT,
            checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (crawl_link_id) REFERENCES crawl_links(id) ON DELETE CASCADE,
            FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_link_check_results_run_url
            ON link_check_results(crawl_run_id, target_url, checked_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            first_seen_run_id INTEGER,
            last_seen_run_id INTEGER,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (first_seen_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (last_seen_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS external_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_url TEXT NOT NULL UNIQUE,
            first_seen_run_id INTEGER,
            last_seen_run_id INTEGER,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (first_seen_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (last_seen_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS run_pages (
            run_id INTEGER NOT NULL,
            page_id INTEGER NOT NULL,
            depth INTEGER NOT NULL,
            status_code INTEGER,
            ok INTEGER NOT NULL,
            error_message TEXT,
            html TEXT,
            main_text TEXT,
            text_similarity_prev REAL,
            text_change_percent_prev REAL,
            text_compared_to_run_id INTEGER,
            text_significant_change INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, page_id),
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE,
            FOREIGN KEY (text_compared_to_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_run_pages_page_id
            ON run_pages(page_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS run_page_external_links (
            run_id INTEGER NOT NULL,
            page_id INTEGER NOT NULL,
            external_link_id INTEGER NOT NULL,
            anchor_text TEXT,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, page_id, external_link_id),
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE,
            FOREIGN KEY (external_link_id) REFERENCES external_links(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_run_page_external_links_run_ext
            ON run_page_external_links(run_id, external_link_id);

        CREATE INDEX IF NOT EXISTS idx_run_page_external_links_run_page
            ON run_page_external_links(run_id, page_id);

        CREATE TABLE IF NOT EXISTS run_external_links (
            run_id INTEGER NOT NULL,
            external_link_id INTEGER NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, external_link_id),
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (external_link_id) REFERENCES external_links(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_run_external_links_external
            ON run_external_links(external_link_id, last_seen_at DESC);

        CREATE TABLE IF NOT EXISTS run_pages_appeared (
            run_id INTEGER NOT NULL,
            compared_to_run_id INTEGER,
            page_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, page_id),
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (compared_to_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS run_pages_disappeared (
            run_id INTEGER NOT NULL,
            compared_to_run_id INTEGER,
            page_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, page_id),
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (compared_to_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS run_external_links_appeared (
            run_id INTEGER NOT NULL,
            compared_to_run_id INTEGER,
            external_link_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, external_link_id),
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (compared_to_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (external_link_id) REFERENCES external_links(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS run_external_links_disappeared (
            run_id INTEGER NOT NULL,
            compared_to_run_id INTEGER,
            external_link_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, external_link_id),
            FOREIGN KEY (run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (compared_to_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (external_link_id) REFERENCES external_links(id) ON DELETE CASCADE
        );
        """
    )
    _ensure_run_pages_text_metric_columns(connection)
    connection.commit()


def _ensure_run_pages_text_metric_columns(connection: sqlite3.Connection) -> None:
    """Add run_pages text metric columns on existing databases (forward-only ALTER)."""
    rows = connection.execute("PRAGMA table_info(run_pages)").fetchall()
    names = {str(r[1]) for r in rows}
    statements: list[str] = []
    if "text_similarity_prev" not in names:
        statements.append("ALTER TABLE run_pages ADD COLUMN text_similarity_prev REAL")
    if "text_change_percent_prev" not in names:
        statements.append("ALTER TABLE run_pages ADD COLUMN text_change_percent_prev REAL")
    if "text_compared_to_run_id" not in names:
        statements.append("ALTER TABLE run_pages ADD COLUMN text_compared_to_run_id INTEGER")
    if "text_significant_change" not in names:
        statements.append("ALTER TABLE run_pages ADD COLUMN text_significant_change INTEGER")
    for stmt in statements:
        connection.execute(stmt)
