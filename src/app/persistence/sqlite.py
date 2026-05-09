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
            link_check_run_id INTEGER,
            target_url TEXT NOT NULL,
            status_code INTEGER,
            ok INTEGER NOT NULL,
            error_message TEXT,
            check_meta TEXT,
            error_category TEXT,
            decision_state TEXT,
            ignore_rule_id INTEGER,
            decision_reason TEXT,
            checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (crawl_link_id) REFERENCES crawl_links(id) ON DELETE CASCADE,
            FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (link_check_run_id) REFERENCES link_check_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (ignore_rule_id) REFERENCES link_ignore_rules(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_link_check_results_run_url
            ON link_check_results(crawl_run_id, target_url, checked_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS link_check_screenshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_check_result_id INTEGER,
            crawl_run_id INTEGER NOT NULL,
            link_check_run_id INTEGER,
            target_url TEXT NOT NULL,
            status_code INTEGER,
            error_message TEXT,
            artifact_file TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (link_check_result_id) REFERENCES link_check_results(id) ON DELETE SET NULL,
            FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (link_check_run_id) REFERENCES link_check_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_link_check_screenshots_result
            ON link_check_screenshots(link_check_result_id);

        CREATE INDEX IF NOT EXISTS idx_link_check_screenshots_run_target
            ON link_check_screenshots(crawl_run_id, target_url, created_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS link_check_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            based_on_crawl_run_id INTEGER NOT NULL,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            checked_total INTEGER NOT NULL DEFAULT 0,
            passed_total INTEGER NOT NULL DEFAULT 0,
            failed_total INTEGER NOT NULL DEFAULT 0,
            errored_total INTEGER NOT NULL DEFAULT 0,
            ignored_total INTEGER NOT NULL DEFAULT 0,
            pending_tolerance_total INTEGER NOT NULL DEFAULT 0,
            reportable_failures_total INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (based_on_crawl_run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_link_check_runs_job_started
            ON link_check_runs(job_id, started_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS link_ignore_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            match_type TEXT NOT NULL,
            pattern TEXT NOT NULL,
            reason TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT,
            created_by TEXT,
            source TEXT NOT NULL DEFAULT 'cli'
        );

        CREATE INDEX IF NOT EXISTS idx_link_ignore_rules_job_active
            ON link_ignore_rules(job_id, active);

        CREATE INDEX IF NOT EXISTS idx_link_ignore_rules_pattern
            ON link_ignore_rules(pattern);

        CREATE TABLE IF NOT EXISTS link_failure_state (
            job_id TEXT NOT NULL,
            target_url TEXT NOT NULL,
            error_category TEXT NOT NULL,
            first_failed_at TEXT NOT NULL,
            last_failed_at TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 1,
            last_status_code INTEGER,
            last_error_message TEXT,
            last_ok_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, target_url, error_category)
        );

        CREATE INDEX IF NOT EXISTS idx_link_failure_state_lookup
            ON link_failure_state(job_id, target_url, error_category);

        CREATE TABLE IF NOT EXISTS link_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            target_url TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            first_reported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_reported_at TEXT,
            last_reported_run_id INTEGER,
            last_seen_checked_at TEXT,
            last_status_code INTEGER,
            last_error_message TEXT,
            reminder_sent_count INTEGER NOT NULL DEFAULT 0,
            hold_until TEXT,
            resolved_at TEXT,
            slack_destination_id TEXT,
            slack_channel_id TEXT,
            slack_root_ts TEXT,
            slack_thread_ts TEXT,
            slack_bootstrap_ts TEXT,
            human_bucket TEXT,
            owner_actor_id TEXT,
            ignore_until TEXT,
            UNIQUE(job_id, target_url)
        );

        CREATE INDEX IF NOT EXISTS idx_link_alerts_open
            ON link_alerts(job_id, state);

        CREATE TABLE IF NOT EXISTS link_alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor_id TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (alert_id) REFERENCES link_alerts(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_link_alert_events_alert
            ON link_alert_events(alert_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS link_retest_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            alert_id INTEGER,
            target_url TEXT NOT NULL,
            slack_destination_id TEXT,
            slack_channel_id TEXT NOT NULL,
            slack_thread_ts TEXT NOT NULL,
            requested_by TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            result_ok INTEGER,
            result_status_code INTEGER,
            result_error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT,
            FOREIGN KEY (alert_id) REFERENCES link_alerts(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_link_retest_queue_pending
            ON link_retest_queue(job_id, status, created_at ASC);

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
    _ensure_link_check_result_decision_columns(connection)
    _ensure_link_check_results_check_meta_column(connection)
    _ensure_link_check_run_columns(connection)
    _ensure_link_alerts_lifecycle_columns(connection)
    _ensure_link_alert_events_table(connection)
    _ensure_link_retest_queue_table(connection)
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


def _ensure_link_check_result_decision_columns(connection: sqlite3.Connection) -> None:
    """Add decision/ignore metadata columns to link_check_results (forward-only ALTER)."""
    rows = connection.execute("PRAGMA table_info(link_check_results)").fetchall()
    names = {str(r[1]) for r in rows}
    statements: list[str] = []
    if "error_category" not in names:
        statements.append("ALTER TABLE link_check_results ADD COLUMN error_category TEXT")
    if "decision_state" not in names:
        statements.append("ALTER TABLE link_check_results ADD COLUMN decision_state TEXT")
    if "ignore_rule_id" not in names:
        statements.append("ALTER TABLE link_check_results ADD COLUMN ignore_rule_id INTEGER")
    if "decision_reason" not in names:
        statements.append("ALTER TABLE link_check_results ADD COLUMN decision_reason TEXT")
    if "link_check_run_id" not in names:
        statements.append("ALTER TABLE link_check_results ADD COLUMN link_check_run_id INTEGER")
    for stmt in statements:
        connection.execute(stmt)


def _ensure_link_check_results_check_meta_column(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_info(link_check_results)").fetchall()
    names = {str(r[1]) for r in rows}
    if "check_meta" not in names:
        connection.execute("ALTER TABLE link_check_results ADD COLUMN check_meta TEXT")


def _ensure_link_check_run_columns(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_info(link_check_screenshots)").fetchall()
    names = {str(r[1]) for r in rows}
    if "link_check_run_id" not in names:
        connection.execute("ALTER TABLE link_check_screenshots ADD COLUMN link_check_run_id INTEGER")


def _ensure_link_alerts_lifecycle_columns(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_info(link_alerts)").fetchall()
    names = {str(r[1]) for r in rows}
    mapping = {
        "slack_destination_id": "ALTER TABLE link_alerts ADD COLUMN slack_destination_id TEXT",
        "slack_channel_id": "ALTER TABLE link_alerts ADD COLUMN slack_channel_id TEXT",
        "slack_root_ts": "ALTER TABLE link_alerts ADD COLUMN slack_root_ts TEXT",
        "slack_thread_ts": "ALTER TABLE link_alerts ADD COLUMN slack_thread_ts TEXT",
        "slack_bootstrap_ts": "ALTER TABLE link_alerts ADD COLUMN slack_bootstrap_ts TEXT",
        "human_bucket": "ALTER TABLE link_alerts ADD COLUMN human_bucket TEXT",
        "owner_actor_id": "ALTER TABLE link_alerts ADD COLUMN owner_actor_id TEXT",
        "ignore_until": "ALTER TABLE link_alerts ADD COLUMN ignore_until TEXT",
    }
    for col, stmt in mapping.items():
        if col not in names:
            connection.execute(stmt)


def _ensure_link_alert_events_table(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='link_alert_events'"
    ).fetchone()
    if row is None:
        connection.executescript(
            """
            CREATE TABLE link_alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (alert_id) REFERENCES link_alerts(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_link_alert_events_alert
                ON link_alert_events(alert_id, created_at DESC);
            """
        )


def _ensure_link_retest_queue_table(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='link_retest_queue'"
    ).fetchone()
    if row is None:
        connection.executescript(
            """
            CREATE TABLE link_retest_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                alert_id INTEGER,
                target_url TEXT NOT NULL,
                slack_destination_id TEXT,
                slack_channel_id TEXT NOT NULL,
                slack_thread_ts TEXT NOT NULL,
                requested_by TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result_ok INTEGER,
                result_status_code INTEGER,
                result_error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT,
                FOREIGN KEY (alert_id) REFERENCES link_alerts(id) ON DELETE SET NULL
            );
            CREATE INDEX idx_link_retest_queue_pending
                ON link_retest_queue(job_id, status, created_at ASC);
            """
        )
