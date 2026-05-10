"""Repository layer for crawl persistence."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from app.crawl.text_compare import main_text_similarity_and_change_percent


@dataclass(frozen=True)
class LinkForCheck:
    link_id: int
    crawl_run_id: int
    target_url: str


@dataclass(frozen=True)
class CrawlRunRecord:
    run_id: int
    started_at: str
    finished_at: str | None
    pages_visited: int
    pages_failed: int
    links_discovered: int


@dataclass(frozen=True)
class CrawlPageRecord:
    run_id: int
    url: str
    depth: int
    status_code: int | None
    ok: bool
    created_at: str
    error_message: str | None


@dataclass(frozen=True)
class ExternalLinkRecord:
    target_url: str
    first_seen_at: str
    seen_count: int


@dataclass(frozen=True)
class PageDiffRecord:
    run_id: int
    compared_to_run_id: int | None
    url: str
    created_at: str


@dataclass(frozen=True)
class ExternalLinkDiffRecord:
    run_id: int
    compared_to_run_id: int | None
    target_url: str
    created_at: str


@dataclass(frozen=True)
class ExternalLinkSourcePageRecord:
    source_page_url: str
    first_seen_at: str


@dataclass(frozen=True)
class ExternalLinkSourceRefRecord:
    source_page_url: str
    anchor_text: str | None


@dataclass(frozen=True)
class PageContentMetricRecord:
    url: str
    text_similarity_prev: float | None
    text_change_percent_prev: float | None
    text_compared_to_run_id: int | None
    text_significant_change: bool | None


@dataclass(frozen=True)
class PageTextRecord:
    run_id: int
    url: str
    created_at: str
    text_len: int
    main_text: str


@dataclass(frozen=True)
class PageExternalCountRecord:
    url: str
    depth: int
    status_code: int | None
    ok: bool
    external_count: int


@dataclass(frozen=True)
class LinkCheckResultRecord:
    row_id: int
    crawl_link_id: int
    crawl_run_id: int
    link_check_run_id: int | None
    target_url: str
    status_code: int | None
    ok: bool
    error_message: str | None
    checked_at: str
    error_category: str | None = None
    decision_state: str | None = None
    ignore_rule_id: int | None = None
    decision_reason: str | None = None
    check_meta: str | None = None


@dataclass(frozen=True)
class LinkCheckRunRecord:
    run_id: int
    job_id: str
    based_on_crawl_run_id: int
    started_at: str
    finished_at: str | None
    checked_total: int
    passed_total: int
    failed_total: int
    errored_total: int
    ignored_total: int


@dataclass(frozen=True)
class LinkCheckScreenshotRecord:
    screenshot_id: int
    link_check_result_id: int | None
    crawl_run_id: int
    link_check_run_id: int | None
    target_url: str
    status_code: int | None
    error_message: str | None
    artifact_file: str
    created_at: str


@dataclass(frozen=True)
class LinkIgnoreRuleRecord:
    rule_id: int
    job_id: str
    match_type: str
    pattern: str
    reason: str | None
    active: bool
    created_at: str
    expires_at: str | None
    created_by: str | None
    source: str


@dataclass(frozen=True)
class LinkFailureStateRecord:
    job_id: str
    target_url: str
    error_category: str
    first_failed_at: str
    last_failed_at: str
    consecutive_failures: int
    last_status_code: int | None
    last_error_message: str | None
    last_ok_at: str | None
    updated_at: str


@dataclass(frozen=True)
class LinkAlertRecord:
    alert_id: int
    job_id: str
    target_url: str
    state: str
    first_reported_at: str
    last_reported_at: str | None
    last_reported_run_id: int | None
    last_seen_checked_at: str | None
    last_status_code: int | None
    last_error_message: str | None
    reminder_sent_count: int
    hold_until: str | None
    resolved_at: str | None
    slack_destination_id: str | None = None
    slack_channel_id: str | None = None
    slack_root_ts: str | None = None
    slack_thread_ts: str | None = None
    slack_bootstrap_ts: str | None = None
    human_bucket: str | None = None
    owner_actor_id: str | None = None
    ignore_until: str | None = None


@dataclass(frozen=True)
class LinkAlertEventRecord:
    event_id: int
    alert_id: int
    event_type: str
    actor_id: str | None
    payload_json: str | None
    created_at: str


@dataclass(frozen=True)
class LinkRetestQueueRecord:
    retest_id: int
    job_id: str
    alert_id: int | None
    target_url: str
    slack_destination_id: str | None
    slack_channel_id: str
    slack_thread_ts: str
    requested_by: str | None
    status: str
    result_ok: bool | None
    result_status_code: int | None
    result_error_message: str | None
    created_at: str
    processed_at: str | None


@dataclass(frozen=True)
class IgnoreRuleImpactRecord:
    rule_id: int
    job_id: str
    match_type: str
    pattern: str
    reason: str | None
    active: bool
    expires_at: str | None
    target_url: str
    source_page_url: str | None
    anchor_text: str | None


class CrawlRepository:
    """Persist crawl run and page/link facts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_run(self, job_id: str) -> int:
        cursor = self._connection.execute(
            "INSERT INTO crawl_runs(job_id) VALUES (?)",
            (job_id,),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, pages_visited: int, pages_failed: int, links_discovered: int) -> None:
        self._connection.execute(
            """
            UPDATE crawl_runs
            SET
                finished_at = CURRENT_TIMESTAMP,
                pages_visited = ?,
                pages_failed = ?,
                links_discovered = ?
            WHERE id = ?
            """,
            (pages_visited, pages_failed, links_discovered, run_id),
        )
        self._connection.commit()

    def add_page_result(
        self,
        run_id: int,
        url: str,
        depth: int,
        status_code: int | None,
        ok: bool,
        error_message: str | None = None,
        html: str | None = None,
        main_text: str | None = None,
    ) -> None:
        page_id = self._upsert_page(url=url, run_id=run_id)
        self._connection.execute(
            """
            INSERT INTO run_pages(
                run_id,
                page_id,
                depth,
                status_code,
                ok,
                error_message,
                html,
                main_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, page_id) DO UPDATE SET
                depth = excluded.depth,
                status_code = excluded.status_code,
                ok = excluded.ok,
                error_message = excluded.error_message,
                html = excluded.html,
                main_text = excluded.main_text,
                created_at = CURRENT_TIMESTAMP
            """,
            (run_id, page_id, depth, status_code, int(ok), error_message, html, main_text),
        )
        # Keep legacy row for transition compatibility.
        self._connection.execute(
            """
            INSERT INTO crawl_pages(
                run_id,
                url,
                depth,
                status_code,
                ok,
                error_message,
                html,
                main_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, url, depth, status_code, int(ok), error_message, html, main_text),
        )
        self._connection.commit()

    def add_link(
        self,
        run_id: int,
        source_url: str,
        target_url: str,
        is_internal: bool,
        anchor_text: str | None = None,
    ) -> None:
        if not is_internal:
            external_link_id = self._upsert_external_link(target_url=target_url, run_id=run_id)
            self._connection.execute(
                """
                INSERT INTO run_external_links(
                    run_id,
                    external_link_id,
                    seen_count
                ) VALUES (?, ?, 1)
                ON CONFLICT(run_id, external_link_id) DO UPDATE SET
                    seen_count = run_external_links.seen_count + 1,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (run_id, external_link_id),
            )
            src = self._connection.execute("SELECT id FROM pages WHERE url = ?", (source_url,)).fetchone()
            if src is not None:
                page_id = int(src["id"])
                self._connection.execute(
                    """
                    INSERT INTO run_page_external_links(run_id, page_id, external_link_id, anchor_text)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(run_id, page_id, external_link_id) DO UPDATE SET
                        anchor_text = CASE
                            WHEN (run_page_external_links.anchor_text IS NULL OR TRIM(run_page_external_links.anchor_text) = '')
                                 AND excluded.anchor_text IS NOT NULL
                                 AND TRIM(excluded.anchor_text) != ''
                            THEN excluded.anchor_text
                            ELSE run_page_external_links.anchor_text
                        END
                    """,
                    (run_id, page_id, external_link_id, anchor_text),
                )
        # Keep legacy row for transition compatibility.
        self._connection.execute(
            """
            INSERT INTO crawl_links(run_id, source_url, target_url, is_internal)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, source_url, target_url, int(is_internal)),
        )
        self._connection.commit()

    def prune_page_history(self, url: str, keep: int) -> None:
        self._connection.execute(
            """
            DELETE FROM crawl_pages
            WHERE id IN (
                SELECT id
                FROM crawl_pages
                WHERE url = ?
                ORDER BY created_at DESC, id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (url, keep),
        )
        self._connection.commit()

    def get_latest_run_id(self, job_id: str) -> int | None:
        row = self._connection.execute(
            """
            SELECT id
            FROM crawl_runs
            WHERE job_id = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def get_previous_run_id(self, job_id: str, run_id: int) -> int | None:
        row = self._connection.execute(
            """
            SELECT id
            FROM crawl_runs
            WHERE job_id = ? AND id < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id, run_id),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def list_links_for_check(self, crawl_run_id: int, limit: int | None = None) -> list[LinkForCheck]:
        query = """
            SELECT
                COALESCE(
                    (
                        SELECT MIN(cl.id)
                        FROM crawl_links cl
                        WHERE cl.run_id = rel.run_id
                          AND cl.is_internal = 0
                          AND cl.target_url = el.target_url
                    ),
                    0
                ) AS link_id,
                rel.run_id AS run_id,
                el.target_url AS target_url
            FROM run_external_links rel
            JOIN external_links el ON el.id = rel.external_link_id
            WHERE rel.run_id = ?
            ORDER BY link_id ASC
        """
        params: tuple[object, ...]
        if limit is None:
            params = (crawl_run_id,)
        else:
            query += " LIMIT ?"
            params = (crawl_run_id, limit)

        rows = self._connection.execute(query, params).fetchall()
        return [
            LinkForCheck(
                link_id=int(row["link_id"]),
                crawl_run_id=int(row["run_id"]),
                target_url=str(row["target_url"]),
            )
            for row in rows
        ]

    def add_link_check_result(
        self,
        crawl_link_id: int,
        crawl_run_id: int,
        link_check_run_id: int | None,
        target_url: str,
        status_code: int | None,
        ok: bool,
        error_message: str | None,
        error_category: str | None = None,
        decision_state: str | None = None,
        ignore_rule_id: int | None = None,
        decision_reason: str | None = None,
        check_meta: str | None = None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO link_check_results(
                crawl_link_id,
                crawl_run_id,
                link_check_run_id,
                target_url,
                status_code,
                ok,
                error_message,
                check_meta,
                error_category,
                decision_state,
                ignore_rule_id,
                decision_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                crawl_link_id,
                crawl_run_id,
                link_check_run_id,
                target_url,
                status_code,
                int(ok),
                error_message,
                check_meta,
                error_category,
                decision_state,
                ignore_rule_id,
                decision_reason,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def add_link_check_screenshot(
        self,
        *,
        link_check_result_id: int | None,
        crawl_run_id: int,
        link_check_run_id: int | None,
        target_url: str,
        status_code: int | None,
        error_message: str | None,
        artifact_file: str,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO link_check_screenshots(
                link_check_result_id,
                crawl_run_id,
                link_check_run_id,
                target_url,
                status_code,
                error_message,
                artifact_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link_check_result_id,
                crawl_run_id,
                link_check_run_id,
                target_url,
                status_code,
                error_message,
                artifact_file,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def list_latest_screenshots_by_result_ids(self, result_ids: list[int]) -> dict[int, LinkCheckScreenshotRecord]:
        if not result_ids:
            return {}
        placeholders = ", ".join(["?"] * len(result_ids))
        rows = self._connection.execute(
            f"""
            SELECT
                s.id,
                s.link_check_result_id,
                s.crawl_run_id,
                s.link_check_run_id,
                s.target_url,
                s.status_code,
                s.error_message,
                s.artifact_file,
                s.created_at
            FROM link_check_screenshots s
            JOIN (
                SELECT link_check_result_id, MAX(id) AS max_id
                FROM link_check_screenshots
                WHERE link_check_result_id IN ({placeholders})
                GROUP BY link_check_result_id
            ) latest ON latest.max_id = s.id
            """,
            tuple(result_ids),
        ).fetchall()
        mapped: dict[int, LinkCheckScreenshotRecord] = {}
        for row in rows:
            result_id = int(row["link_check_result_id"])
            mapped[result_id] = LinkCheckScreenshotRecord(
                screenshot_id=int(row["id"]),
                link_check_result_id=result_id,
                crawl_run_id=int(row["crawl_run_id"]),
                link_check_run_id=int(row["link_check_run_id"]) if row["link_check_run_id"] is not None else None,
                target_url=str(row["target_url"]),
                status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                error_message=str(row["error_message"]) if row["error_message"] is not None else None,
                artifact_file=str(row["artifact_file"]),
                created_at=str(row["created_at"]),
            )
        return mapped

    def list_latest_link_check_results(
        self,
        crawl_run_id: int,
        link_check_run_id: int | None = None,
        limit: int | None = None,
    ) -> list[LinkCheckResultRecord]:
        if link_check_run_id is not None:
            query = """
            SELECT
                l.id,
                l.crawl_link_id,
                l.crawl_run_id,
                l.link_check_run_id,
                l.target_url,
                l.status_code,
                l.ok,
                l.error_message,
                l.check_meta,
                l.checked_at,
                l.error_category,
                l.decision_state,
                l.ignore_rule_id,
                l.decision_reason
            FROM link_check_results l
            WHERE l.link_check_run_id = ?
            ORDER BY l.target_url ASC
            """
            params: list[object] = [link_check_run_id]
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            rows = self._connection.execute(query, tuple(params)).fetchall()
            return [
                LinkCheckResultRecord(
                    row_id=int(row["id"]),
                    crawl_link_id=int(row["crawl_link_id"]),
                    crawl_run_id=int(row["crawl_run_id"]),
                    link_check_run_id=int(row["link_check_run_id"]) if row["link_check_run_id"] is not None else None,
                    target_url=str(row["target_url"]),
                    status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                    ok=bool(row["ok"]),
                    error_message=str(row["error_message"]) if row["error_message"] is not None else None,
                    checked_at=str(row["checked_at"]),
                    error_category=str(row["error_category"]) if row["error_category"] is not None else None,
                    decision_state=str(row["decision_state"]) if row["decision_state"] is not None else None,
                    ignore_rule_id=int(row["ignore_rule_id"]) if row["ignore_rule_id"] is not None else None,
                    decision_reason=str(row["decision_reason"]) if row["decision_reason"] is not None else None,
                    check_meta=str(row["check_meta"]) if row["check_meta"] is not None else None,
                )
                for row in rows
            ]

        query = """
            SELECT
                l.id,
                l.crawl_link_id,
                l.crawl_run_id,
                l.link_check_run_id,
                l.target_url,
                l.status_code,
                l.ok,
                l.error_message,
                l.check_meta,
                l.checked_at,
                l.error_category,
                l.decision_state,
                l.ignore_rule_id,
                l.decision_reason
            FROM link_check_results l
            JOIN (
                SELECT target_url, MAX(id) AS max_id
                FROM link_check_results
                WHERE crawl_run_id = ?
                GROUP BY target_url
            ) latest ON latest.max_id = l.id
            WHERE l.crawl_run_id = ?
            ORDER BY l.target_url ASC
        """
        params: list[object] = [crawl_run_id, crawl_run_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._connection.execute(query, tuple(params)).fetchall()
        return [
            LinkCheckResultRecord(
                row_id=int(row["id"]),
                crawl_link_id=int(row["crawl_link_id"]),
                crawl_run_id=int(row["crawl_run_id"]),
                link_check_run_id=int(row["link_check_run_id"]) if row["link_check_run_id"] is not None else None,
                target_url=str(row["target_url"]),
                status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                ok=bool(row["ok"]),
                error_message=str(row["error_message"]) if row["error_message"] is not None else None,
                checked_at=str(row["checked_at"]),
                error_category=str(row["error_category"]) if row["error_category"] is not None else None,
                decision_state=str(row["decision_state"]) if row["decision_state"] is not None else None,
                ignore_rule_id=int(row["ignore_rule_id"]) if row["ignore_rule_id"] is not None else None,
                decision_reason=str(row["decision_reason"]) if row["decision_reason"] is not None else None,
                check_meta=str(row["check_meta"]) if row["check_meta"] is not None else None,
            )
            for row in rows
        ]

    def list_latest_failed_link_check_results(
        self,
        crawl_run_id: int,
        link_check_run_id: int | None = None,
        limit: int = 200,
    ) -> list[LinkCheckResultRecord]:
        if link_check_run_id is not None:
            rows = self._connection.execute(
                """
                SELECT
                    l.id,
                    l.crawl_link_id,
                    l.crawl_run_id,
                    l.link_check_run_id,
                    l.target_url,
                    l.status_code,
                    l.ok,
                    l.error_message,
                    l.check_meta,
                    l.checked_at,
                    l.error_category,
                    l.decision_state,
                    l.ignore_rule_id,
                    l.decision_reason
                FROM link_check_results l
                WHERE l.link_check_run_id = ?
                  AND l.ok = 0
                ORDER BY l.checked_at DESC, l.target_url ASC
                LIMIT ?
                """,
                (link_check_run_id, limit),
            ).fetchall()
            return [
                LinkCheckResultRecord(
                    row_id=int(row["id"]),
                    crawl_link_id=int(row["crawl_link_id"]),
                    crawl_run_id=int(row["crawl_run_id"]),
                    link_check_run_id=int(row["link_check_run_id"]) if row["link_check_run_id"] is not None else None,
                    target_url=str(row["target_url"]),
                    status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                    ok=bool(row["ok"]),
                    error_message=str(row["error_message"]) if row["error_message"] is not None else None,
                    checked_at=str(row["checked_at"]),
                    error_category=str(row["error_category"]) if row["error_category"] is not None else None,
                    decision_state=str(row["decision_state"]) if row["decision_state"] is not None else None,
                    ignore_rule_id=int(row["ignore_rule_id"]) if row["ignore_rule_id"] is not None else None,
                    decision_reason=str(row["decision_reason"]) if row["decision_reason"] is not None else None,
                    check_meta=str(row["check_meta"]) if row["check_meta"] is not None else None,
                )
                for row in rows
            ]

        rows = self._connection.execute(
            """
            SELECT
                l.id,
                l.crawl_link_id,
                l.crawl_run_id,
                l.link_check_run_id,
                l.target_url,
                l.status_code,
                l.ok,
                l.error_message,
                l.check_meta,
                l.checked_at,
                l.error_category,
                l.decision_state,
                l.ignore_rule_id,
                l.decision_reason
            FROM link_check_results l
            JOIN (
                SELECT target_url, MAX(id) AS max_id
                FROM link_check_results
                WHERE crawl_run_id = ?
                GROUP BY target_url
            ) latest ON latest.max_id = l.id
            WHERE l.crawl_run_id = ?
              AND l.ok = 0
            ORDER BY l.checked_at DESC, l.target_url ASC
            LIMIT ?
            """,
            (crawl_run_id, crawl_run_id, limit),
        ).fetchall()
        return [
            LinkCheckResultRecord(
                row_id=int(row["id"]),
                crawl_link_id=int(row["crawl_link_id"]),
                crawl_run_id=int(row["crawl_run_id"]),
                link_check_run_id=int(row["link_check_run_id"]) if row["link_check_run_id"] is not None else None,
                target_url=str(row["target_url"]),
                status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                ok=bool(row["ok"]),
                error_message=str(row["error_message"]) if row["error_message"] is not None else None,
                checked_at=str(row["checked_at"]),
                error_category=str(row["error_category"]) if row["error_category"] is not None else None,
                decision_state=str(row["decision_state"]) if row["decision_state"] is not None else None,
                ignore_rule_id=int(row["ignore_rule_id"]) if row["ignore_rule_id"] is not None else None,
                decision_reason=str(row["decision_reason"]) if row["decision_reason"] is not None else None,
                check_meta=str(row["check_meta"]) if row["check_meta"] is not None else None,
            )
            for row in rows
        ]

    def list_link_check_run_history(self, job_id: str, *, limit: int = 100) -> list[LinkCheckRunRecord]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                job_id,
                based_on_crawl_run_id,
                started_at,
                finished_at,
                checked_total,
                passed_total,
                failed_total,
                errored_total,
                ignored_total
            FROM link_check_runs
            WHERE job_id = ?
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (job_id, limit),
        ).fetchall()
        return [
            LinkCheckRunRecord(
                run_id=int(row["id"]),
                job_id=str(row["job_id"]),
                based_on_crawl_run_id=int(row["based_on_crawl_run_id"]),
                started_at=str(row["started_at"]),
                finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
                checked_total=int(row["checked_total"] or 0),
                passed_total=int(row["passed_total"] or 0),
                failed_total=int(row["failed_total"] or 0),
                errored_total=int(row["errored_total"] or 0),
                ignored_total=int(row["ignored_total"] or 0),
            )
            for row in rows
        ]

    def create_link_check_run(self, *, job_id: str, based_on_crawl_run_id: int, started_at: str) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO link_check_runs(job_id, based_on_crawl_run_id, started_at)
            VALUES (?, ?, ?)
            """,
            (job_id, based_on_crawl_run_id, started_at),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def finish_link_check_run(
        self,
        *,
        link_check_run_id: int,
        finished_at: str,
        checked_total: int,
        passed_total: int,
        failed_total: int,
        errored_total: int,
        ignored_total: int,
        pending_tolerance_total: int,
        reportable_failures_total: int,
    ) -> None:
        self._connection.execute(
            """
            UPDATE link_check_runs
            SET finished_at = ?,
                checked_total = ?,
                passed_total = ?,
                failed_total = ?,
                errored_total = ?,
                ignored_total = ?,
                pending_tolerance_total = ?,
                reportable_failures_total = ?
            WHERE id = ?
            """,
            (
                finished_at,
                checked_total,
                passed_total,
                failed_total,
                errored_total,
                ignored_total,
                pending_tolerance_total,
                reportable_failures_total,
                link_check_run_id,
            ),
        )
        self._connection.commit()

    def get_latest_link_check_run_id_for_crawl(self, crawl_run_id: int) -> int | None:
        """Return the most recent link_check_runs.id for this crawl run, if any."""
        row = self._connection.execute(
            """
            SELECT id
            FROM link_check_runs
            WHERE based_on_crawl_run_id = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (crawl_run_id,),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def get_link_check_run(self, link_check_run_id: int) -> LinkCheckRunRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                job_id,
                based_on_crawl_run_id,
                started_at,
                finished_at,
                checked_total,
                passed_total,
                failed_total,
                errored_total,
                ignored_total
            FROM link_check_runs
            WHERE id = ?
            """,
            (link_check_run_id,),
        ).fetchone()
        if row is None:
            return None
        return LinkCheckRunRecord(
            run_id=int(row["id"]),
            job_id=str(row["job_id"]),
            based_on_crawl_run_id=int(row["based_on_crawl_run_id"]),
            started_at=str(row["started_at"]),
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
            checked_total=int(row["checked_total"] or 0),
            passed_total=int(row["passed_total"] or 0),
            failed_total=int(row["failed_total"] or 0),
            errored_total=int(row["errored_total"] or 0),
            ignored_total=int(row["ignored_total"] or 0),
        )

    def list_source_page_refs_for_targets(self, run_id: int, target_urls: list[str]) -> dict[str, list[ExternalLinkSourceRefRecord]]:
        if not target_urls:
            return {}
        placeholders = ", ".join(["?"] * len(target_urls))
        rows = self._connection.execute(
            f"""
            SELECT
                el.target_url AS target_url,
                p.url AS source_page_url,
                rpe.anchor_text AS anchor_text
            FROM run_page_external_links rpe
            JOIN pages p ON p.id = rpe.page_id
            JOIN external_links el ON el.id = rpe.external_link_id
            WHERE rpe.run_id = ? AND el.target_url IN ({placeholders})
            ORDER BY el.target_url ASC, p.url ASC
            """,
            (run_id, *target_urls),
        ).fetchall()
        mapped: dict[str, list[ExternalLinkSourceRefRecord]] = defaultdict(list)
        for row in rows:
            mapped[str(row["target_url"])].append(
                ExternalLinkSourceRefRecord(
                    source_page_url=str(row["source_page_url"]),
                    anchor_text=str(row["anchor_text"]) if row["anchor_text"] is not None else None,
                )
            )
        return dict(mapped)

    def list_source_pages_for_targets(self, run_id: int, target_urls: list[str]) -> dict[str, list[str]]:
        refs = self.list_source_page_refs_for_targets(run_id, target_urls)
        return {target: [ref.source_page_url for ref in records] for target, records in refs.items()}

    def get_run_started_finished(self, run_id: int) -> tuple[str | None, str | None]:
        row = self._connection.execute(
            "SELECT started_at, finished_at FROM crawl_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None, None
        started_at = str(row["started_at"]) if row["started_at"] is not None else None
        finished_at = str(row["finished_at"]) if row["finished_at"] is not None else None
        return started_at, finished_at

    def add_link_ignore_rule(
        self,
        *,
        job_id: str,
        match_type: str,
        pattern: str,
        reason: str | None = None,
        expires_at: str | None = None,
        created_by: str | None = None,
        source: str = "cli",
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO link_ignore_rules(job_id, match_type, pattern, reason, expires_at, created_by, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, match_type, pattern, reason, expires_at, created_by, source),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def deactivate_link_ignore_rule(self, *, job_id: str, rule_id: int) -> bool:
        cursor = self._connection.execute(
            """
            UPDATE link_ignore_rules
            SET active = 0
            WHERE id = ? AND job_id = ? AND active = 1
            """,
            (rule_id, job_id),
        )
        self._connection.commit()
        return int(cursor.rowcount) > 0

    def list_link_ignore_rules(
        self,
        *,
        job_id: str,
        active_only: bool = False,
        search: str | None = None,
        limit: int = 200,
    ) -> list[LinkIgnoreRuleRecord]:
        query = """
            SELECT id, job_id, match_type, pattern, reason, active, created_at, expires_at, created_by, source
            FROM link_ignore_rules
            WHERE job_id = ?
        """
        params: list[object] = [job_id]
        if active_only:
            query += " AND active = 1"
        if search:
            query += " AND LOWER(pattern) LIKE ?"
            params.append(f"%{search.lower()}%")
        query += " ORDER BY active DESC, created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = self._connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_ignore_rule(row) for row in rows]

    def list_ignore_rule_impacts(
        self,
        *,
        job_id: str,
        run_id: int,
        active_only: bool = True,
        rule_id: int | None = None,
        limit: int = 2000,
        now: datetime | None = None,
    ) -> list[IgnoreRuleImpactRecord]:
        now_str = (now or datetime.now(tz=UTC)).isoformat()
        query = """
            SELECT
                r.id AS rule_id,
                r.job_id AS job_id,
                r.match_type AS match_type,
                r.pattern AS pattern,
                r.reason AS reason,
                r.active AS active,
                r.expires_at AS expires_at,
                el.target_url AS target_url,
                p.url AS source_page_url,
                rpe.anchor_text AS anchor_text
            FROM link_ignore_rules r
            JOIN run_external_links rel ON rel.run_id = ?
            JOIN external_links el ON el.id = rel.external_link_id
            LEFT JOIN run_page_external_links rpe
                ON rpe.run_id = rel.run_id
               AND rpe.external_link_id = rel.external_link_id
            LEFT JOIN pages p ON p.id = rpe.page_id
            WHERE r.job_id = ?
              AND (r.expires_at IS NULL OR r.expires_at > ?)
              AND (
                (r.match_type = 'exact' AND LOWER(r.pattern) = LOWER(el.target_url))
                OR
                (r.match_type = 'contains' AND INSTR(LOWER(el.target_url), LOWER(r.pattern)) > 0)
              )
        """
        params: list[object] = [run_id, job_id, now_str]
        if active_only:
            query += " AND r.active = 1"
        if rule_id is not None:
            query += " AND r.id = ?"
            params.append(rule_id)
        query += """
            ORDER BY
                r.id ASC,
                el.target_url ASC,
                p.url ASC
            LIMIT ?
        """
        params.append(limit)
        rows = self._connection.execute(query, tuple(params)).fetchall()
        return [
            IgnoreRuleImpactRecord(
                rule_id=int(row["rule_id"]),
                job_id=str(row["job_id"]),
                match_type=str(row["match_type"]),
                pattern=str(row["pattern"]),
                reason=str(row["reason"]) if row["reason"] is not None else None,
                active=bool(row["active"]),
                expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
                target_url=str(row["target_url"]),
                source_page_url=str(row["source_page_url"]) if row["source_page_url"] is not None else None,
                anchor_text=str(row["anchor_text"]) if row["anchor_text"] is not None else None,
            )
            for row in rows
        ]

    def find_matching_link_ignore_rule(
        self,
        *,
        job_id: str,
        target_url: str,
        now: datetime | None = None,
    ) -> LinkIgnoreRuleRecord | None:
        now_str = (now or datetime.now(tz=UTC)).isoformat()
        row = self._connection.execute(
            """
            SELECT id, job_id, match_type, pattern, reason, active, created_at, expires_at, created_by, source
            FROM link_ignore_rules
            WHERE job_id = ?
              AND active = 1
              AND (expires_at IS NULL OR expires_at > ?)
              AND (
                (match_type = 'exact' AND LOWER(pattern) = LOWER(?))
                OR
                (match_type = 'contains' AND INSTR(LOWER(?), LOWER(pattern)) > 0)
              )
            ORDER BY
              CASE WHEN match_type = 'exact' THEN 0 ELSE 1 END ASC,
              LENGTH(pattern) DESC,
              id ASC
            LIMIT 1
            """,
            (job_id, now_str, target_url, target_url),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_ignore_rule(row)

    def record_link_failure_state(
        self,
        *,
        job_id: str,
        target_url: str,
        error_category: str,
        status_code: int | None,
        error_message: str | None,
        failed_at: datetime | None = None,
    ) -> LinkFailureStateRecord:
        failed_at_str = (failed_at or datetime.now(tz=UTC)).isoformat()
        self._connection.execute(
            """
            INSERT INTO link_failure_state(
                job_id,
                target_url,
                error_category,
                first_failed_at,
                last_failed_at,
                consecutive_failures,
                last_status_code,
                last_error_message,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(job_id, target_url, error_category) DO UPDATE SET
                last_failed_at = excluded.last_failed_at,
                consecutive_failures = link_failure_state.consecutive_failures + 1,
                last_status_code = excluded.last_status_code,
                last_error_message = excluded.last_error_message,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                target_url,
                error_category,
                failed_at_str,
                failed_at_str,
                status_code,
                error_message,
                failed_at_str,
            ),
        )
        row = self._connection.execute(
            """
            SELECT
                job_id,
                target_url,
                error_category,
                first_failed_at,
                last_failed_at,
                consecutive_failures,
                last_status_code,
                last_error_message,
                last_ok_at,
                updated_at
            FROM link_failure_state
            WHERE job_id = ? AND target_url = ? AND error_category = ?
            """,
            (job_id, target_url, error_category),
        ).fetchone()
        self._connection.commit()
        if row is None:
            raise RuntimeError("link_failure_state row missing after upsert")
        return self._row_to_link_failure_state(row)

    def clear_link_failure_state(
        self,
        *,
        job_id: str,
        target_url: str,
        error_category: str | None = None,
    ) -> int:
        if error_category is None:
            cursor = self._connection.execute(
                "DELETE FROM link_failure_state WHERE job_id = ? AND target_url = ?",
                (job_id, target_url),
            )
        else:
            cursor = self._connection.execute(
                "DELETE FROM link_failure_state WHERE job_id = ? AND target_url = ? AND error_category = ?",
                (job_id, target_url, error_category),
            )
        self._connection.commit()
        return int(cursor.rowcount)

    def get_link_failure_state(
        self,
        *,
        job_id: str,
        target_url: str,
        error_category: str,
    ) -> LinkFailureStateRecord | None:
        row = self._connection.execute(
            """
            SELECT
                job_id,
                target_url,
                error_category,
                first_failed_at,
                last_failed_at,
                consecutive_failures,
                last_status_code,
                last_error_message,
                last_ok_at,
                updated_at
            FROM link_failure_state
            WHERE job_id = ? AND target_url = ? AND error_category = ?
            """,
            (job_id, target_url, error_category),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_link_failure_state(row)

    _LINK_ALERT_SELECT = """
                id,
                job_id,
                target_url,
                state,
                first_reported_at,
                last_reported_at,
                last_reported_run_id,
                last_seen_checked_at,
                last_status_code,
                last_error_message,
                reminder_sent_count,
                hold_until,
                resolved_at,
                slack_destination_id,
                slack_channel_id,
                slack_root_ts,
                slack_thread_ts,
                slack_bootstrap_ts,
                human_bucket,
                owner_actor_id,
                ignore_until
    """

    def list_open_link_alerts(self, *, job_id: str) -> list[LinkAlertRecord]:
        rows = self._connection.execute(
            f"""
            SELECT
                {self._LINK_ALERT_SELECT.strip()}
            FROM link_alerts
            WHERE job_id = ? AND state = 'open'
            ORDER BY first_reported_at ASC, id ASC
            """,
            (job_id,),
        ).fetchall()
        return [self._row_to_link_alert(row) for row in rows]

    def upsert_open_link_alert(
        self,
        *,
        job_id: str,
        target_url: str,
        run_id: int,
        checked_at: str,
        status_code: int | None,
        error_message: str | None,
    ) -> LinkAlertRecord:
        self._connection.execute(
            """
            INSERT INTO link_alerts(
                job_id,
                target_url,
                state,
                first_reported_at,
                last_reported_at,
                last_reported_run_id,
                last_seen_checked_at,
                last_status_code,
                last_error_message
            ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, target_url) DO UPDATE SET
                state = 'open',
                last_reported_at = excluded.last_reported_at,
                last_reported_run_id = excluded.last_reported_run_id,
                last_seen_checked_at = excluded.last_seen_checked_at,
                last_status_code = excluded.last_status_code,
                last_error_message = excluded.last_error_message,
                resolved_at = NULL
            """,
            (job_id, target_url, checked_at, checked_at, run_id, checked_at, status_code, error_message),
        )
        row = self._connection.execute(
            f"""
            SELECT
                {self._LINK_ALERT_SELECT.strip()}
            FROM link_alerts
            WHERE job_id = ? AND target_url = ?
            LIMIT 1
            """,
            (job_id, target_url),
        ).fetchone()
        self._connection.commit()
        if row is None:
            raise RuntimeError("link_alert row missing after upsert")
        return self._row_to_link_alert(row)

    def get_open_link_alert_by_id(self, *, alert_id: int) -> LinkAlertRecord | None:
        row = self._connection.execute(
            f"""
            SELECT
                {self._LINK_ALERT_SELECT.strip()}
            FROM link_alerts
            WHERE id = ? AND state = 'open'
            LIMIT 1
            """,
            (alert_id,),
        ).fetchone()
        return self._row_to_link_alert(row) if row is not None else None

    def get_open_link_alert_by_slack_message(
        self,
        *,
        job_id: str,
        slack_channel_id: str,
        message_ts: str,
    ) -> LinkAlertRecord | None:
        row = self._connection.execute(
            f"""
            SELECT
                {self._LINK_ALERT_SELECT.strip()}
            FROM link_alerts
            WHERE job_id = ?
              AND state = 'open'
              AND slack_channel_id = ?
              AND (slack_root_ts = ? OR slack_bootstrap_ts = ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id, slack_channel_id, message_ts, message_ts),
        ).fetchone()
        return self._row_to_link_alert(row) if row is not None else None

    def get_open_link_alert_by_target(self, *, job_id: str, target_url: str) -> LinkAlertRecord | None:
        row = self._connection.execute(
            f"""
            SELECT
                {self._LINK_ALERT_SELECT.strip()}
            FROM link_alerts
            WHERE job_id = ? AND target_url = ? AND state = 'open'
            LIMIT 1
            """,
            (job_id, target_url),
        ).fetchone()
        return self._row_to_link_alert(row) if row is not None else None

    def update_link_alert_slack_refs(
        self,
        *,
        alert_id: int,
        slack_destination_id: str | None,
        slack_channel_id: str | None,
        slack_root_ts: str | None,
        slack_thread_ts: str | None = None,
        slack_bootstrap_ts: str | None = None,
    ) -> None:
        thread_ts = slack_thread_ts if slack_thread_ts is not None else slack_root_ts
        self._connection.execute(
            """
            UPDATE link_alerts
            SET
                slack_destination_id = COALESCE(?, slack_destination_id),
                slack_channel_id = COALESCE(?, slack_channel_id),
                slack_root_ts = COALESCE(?, slack_root_ts),
                slack_thread_ts = COALESCE(?, slack_thread_ts),
                slack_bootstrap_ts = COALESCE(?, slack_bootstrap_ts)
            WHERE id = ?
            """,
            (
                slack_destination_id,
                slack_channel_id,
                slack_root_ts,
                thread_ts,
                slack_bootstrap_ts,
                alert_id,
            ),
        )
        self._connection.commit()

    def append_link_alert_event(
        self,
        *,
        alert_id: int,
        event_type: str,
        actor_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> int:
        payload_json = json.dumps(payload, sort_keys=True) if payload is not None else None
        cursor = self._connection.execute(
            """
            INSERT INTO link_alert_events(alert_id, event_type, actor_id, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (alert_id, event_type, actor_id, payload_json),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def enqueue_link_retest(
        self,
        *,
        job_id: str,
        alert_id: int | None,
        target_url: str,
        slack_destination_id: str | None,
        slack_channel_id: str,
        slack_thread_ts: str,
        requested_by: str | None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO link_retest_queue(
                job_id,
                alert_id,
                target_url,
                slack_destination_id,
                slack_channel_id,
                slack_thread_ts,
                requested_by,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (job_id, alert_id, target_url, slack_destination_id, slack_channel_id, slack_thread_ts, requested_by),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def list_pending_link_retests(self, *, job_id: str, limit: int = 20) -> list[LinkRetestQueueRecord]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                job_id,
                alert_id,
                target_url,
                slack_destination_id,
                slack_channel_id,
                slack_thread_ts,
                requested_by,
                status,
                result_ok,
                result_status_code,
                result_error_message,
                created_at,
                processed_at
            FROM link_retest_queue
            WHERE job_id = ? AND status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (job_id, limit),
        ).fetchall()
        return [self._row_to_link_retest(row) for row in rows]

    def complete_link_retest(
        self,
        *,
        retest_id: int,
        result_ok: bool,
        status_code: int | None,
        error_message: str | None,
        processed_at: str,
    ) -> None:
        self._connection.execute(
            """
            UPDATE link_retest_queue
            SET
                status = 'done',
                result_ok = ?,
                result_status_code = ?,
                result_error_message = ?,
                processed_at = ?
            WHERE id = ?
            """,
            (
                1 if result_ok else 0,
                status_code,
                error_message,
                processed_at,
                retest_id,
            ),
        )
        self._connection.commit()

    def update_link_alert_lifecycle_fields(
        self,
        *,
        alert_id: int,
        human_bucket: str | None = None,
        owner_actor_id: str | None = None,
        clear_owner: bool = False,
        hold_until: str | None = None,
        clear_hold: bool = False,
        ignore_until: str | None = None,
        clear_ignore: bool = False,
    ) -> None:
        sets: list[str] = []
        params: list[object] = []
        if human_bucket is not None:
            sets.append("human_bucket = ?")
            params.append(human_bucket)
        if clear_owner:
            sets.append("owner_actor_id = NULL")
        elif owner_actor_id is not None:
            sets.append("owner_actor_id = ?")
            params.append(owner_actor_id)
        if clear_hold:
            sets.append("hold_until = NULL")
        elif hold_until is not None:
            sets.append("hold_until = ?")
            params.append(hold_until)
        if clear_ignore:
            sets.append("ignore_until = NULL")
        elif ignore_until is not None:
            sets.append("ignore_until = ?")
            params.append(ignore_until)
        if not sets:
            return
        params.append(alert_id)
        self._connection.execute(
            f"UPDATE link_alerts SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        self._connection.commit()

    def resolve_open_link_alert_by_id(self, *, alert_id: int, resolved_at: str) -> bool:
        cursor = self._connection.execute(
            """
            UPDATE link_alerts
            SET
                state = 'resolved',
                resolved_at = ?,
                human_bucket = NULL,
                owner_actor_id = NULL,
                hold_until = NULL,
                ignore_until = NULL
            WHERE id = ? AND state = 'open'
            """,
            (resolved_at, alert_id),
        )
        self._connection.commit()
        return int(cursor.rowcount) > 0

    def expire_link_alert_human_buckets(self, *, job_id: str, now_iso: str) -> int:
        """Clear timed ignore when past ignore_until; leave infinite (NULL) ignores untouched."""
        cursor = self._connection.execute(
            """
            UPDATE link_alerts
            SET human_bucket = NULL, ignore_until = NULL
            WHERE job_id = ?
              AND state = 'open'
              AND human_bucket = 'ignored'
              AND ignore_until IS NOT NULL
              AND ignore_until <= ?
            """,
            (job_id, now_iso),
        )
        self._connection.commit()
        return int(cursor.rowcount)

    def increment_link_alert_reminder_count(self, *, job_id: str, target_url: str) -> None:
        self._connection.execute(
            """
            UPDATE link_alerts
            SET reminder_sent_count = reminder_sent_count + 1
            WHERE job_id = ? AND target_url = ? AND state = 'open'
            """,
            (job_id, target_url),
        )
        self._connection.commit()

    def resolve_link_alerts_not_in_targets(self, *, job_id: str, active_targets: set[str], resolved_at: str) -> int:
        open_rows = self._connection.execute(
            "SELECT target_url FROM link_alerts WHERE job_id = ? AND state = 'open'",
            (job_id,),
        ).fetchall()
        to_resolve = [str(row["target_url"]) for row in open_rows if str(row["target_url"]) not in active_targets]
        if not to_resolve:
            return 0
        placeholders = ", ".join(["?"] * len(to_resolve))
        cursor = self._connection.execute(
            f"""
            UPDATE link_alerts
            SET state = 'resolved', resolved_at = ?
            WHERE job_id = ? AND state = 'open' AND target_url IN ({placeholders})
            """,
            (resolved_at, job_id, *to_resolve),
        )
        self._connection.commit()
        return int(cursor.rowcount)

    def _row_to_ignore_rule(self, row: sqlite3.Row) -> LinkIgnoreRuleRecord:
        return LinkIgnoreRuleRecord(
            rule_id=int(row["id"]),
            job_id=str(row["job_id"]),
            match_type=str(row["match_type"]),
            pattern=str(row["pattern"]),
            reason=str(row["reason"]) if row["reason"] is not None else None,
            active=bool(row["active"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
            created_by=str(row["created_by"]) if row["created_by"] is not None else None,
            source=str(row["source"]) if row["source"] is not None else "cli",
        )

    def _row_to_link_failure_state(self, row: sqlite3.Row) -> LinkFailureStateRecord:
        return LinkFailureStateRecord(
            job_id=str(row["job_id"]),
            target_url=str(row["target_url"]),
            error_category=str(row["error_category"]),
            first_failed_at=str(row["first_failed_at"]),
            last_failed_at=str(row["last_failed_at"]),
            consecutive_failures=int(row["consecutive_failures"]),
            last_status_code=int(row["last_status_code"]) if row["last_status_code"] is not None else None,
            last_error_message=str(row["last_error_message"]) if row["last_error_message"] is not None else None,
            last_ok_at=str(row["last_ok_at"]) if row["last_ok_at"] is not None else None,
            updated_at=str(row["updated_at"]),
        )

    def _row_to_link_alert(self, row: sqlite3.Row) -> LinkAlertRecord:
        return LinkAlertRecord(
            alert_id=int(row["id"]),
            job_id=str(row["job_id"]),
            target_url=str(row["target_url"]),
            state=str(row["state"]),
            first_reported_at=str(row["first_reported_at"]),
            last_reported_at=str(row["last_reported_at"]) if row["last_reported_at"] is not None else None,
            last_reported_run_id=int(row["last_reported_run_id"]) if row["last_reported_run_id"] is not None else None,
            last_seen_checked_at=str(row["last_seen_checked_at"]) if row["last_seen_checked_at"] is not None else None,
            last_status_code=int(row["last_status_code"]) if row["last_status_code"] is not None else None,
            last_error_message=str(row["last_error_message"]) if row["last_error_message"] is not None else None,
            reminder_sent_count=int(row["reminder_sent_count"]),
            hold_until=str(row["hold_until"]) if row["hold_until"] is not None else None,
            resolved_at=str(row["resolved_at"]) if row["resolved_at"] is not None else None,
            slack_destination_id=str(row["slack_destination_id"]) if row["slack_destination_id"] is not None else None,
            slack_channel_id=str(row["slack_channel_id"]) if row["slack_channel_id"] is not None else None,
            slack_root_ts=str(row["slack_root_ts"]) if row["slack_root_ts"] is not None else None,
            slack_thread_ts=str(row["slack_thread_ts"]) if row["slack_thread_ts"] is not None else None,
            slack_bootstrap_ts=str(row["slack_bootstrap_ts"]) if row["slack_bootstrap_ts"] is not None else None,
            human_bucket=str(row["human_bucket"]) if row["human_bucket"] is not None else None,
            owner_actor_id=str(row["owner_actor_id"]) if row["owner_actor_id"] is not None else None,
            ignore_until=str(row["ignore_until"]) if row["ignore_until"] is not None else None,
        )

    def _row_to_link_retest(self, row: sqlite3.Row) -> LinkRetestQueueRecord:
        rok = row["result_ok"]
        return LinkRetestQueueRecord(
            retest_id=int(row["id"]),
            job_id=str(row["job_id"]),
            alert_id=int(row["alert_id"]) if row["alert_id"] is not None else None,
            target_url=str(row["target_url"]),
            slack_destination_id=str(row["slack_destination_id"]) if row["slack_destination_id"] is not None else None,
            slack_channel_id=str(row["slack_channel_id"]),
            slack_thread_ts=str(row["slack_thread_ts"]),
            requested_by=str(row["requested_by"]) if row["requested_by"] is not None else None,
            status=str(row["status"]),
            result_ok=bool(rok) if rok is not None else None,
            result_status_code=int(row["result_status_code"]) if row["result_status_code"] is not None else None,
            result_error_message=str(row["result_error_message"]) if row["result_error_message"] is not None else None,
            created_at=str(row["created_at"]),
            processed_at=str(row["processed_at"]) if row["processed_at"] is not None else None,
        )

    def _upsert_page(self, url: str, run_id: int) -> int:
        row = self._connection.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()
        if row is not None:
            page_id = int(row["id"])
            self._connection.execute(
                """
                UPDATE pages
                SET last_seen_run_id = ?, last_seen_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id, page_id),
            )
            return page_id
        cursor = self._connection.execute(
            """
            INSERT INTO pages(url, first_seen_run_id, last_seen_run_id)
            VALUES (?, ?, ?)
            """,
            (url, run_id, run_id),
        )
        return int(cursor.lastrowid)

    def _upsert_external_link(self, target_url: str, run_id: int) -> int:
        row = self._connection.execute(
            "SELECT id FROM external_links WHERE target_url = ?",
            (target_url,),
        ).fetchone()
        if row is not None:
            external_id = int(row["id"])
            self._connection.execute(
                """
                UPDATE external_links
                SET last_seen_run_id = ?, last_seen_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id, external_id),
            )
            return external_id
        cursor = self._connection.execute(
            """
            INSERT INTO external_links(target_url, first_seen_run_id, last_seen_run_id)
            VALUES (?, ?, ?)
            """,
            (target_url, run_id, run_id),
        )
        return int(cursor.lastrowid)

    def list_source_pages_for_external(
        self,
        run_id: int,
        *,
        target_url: str | None = None,
        external_link_id: int | None = None,
        limit: int = 500,
    ) -> list[ExternalLinkSourcePageRecord]:
        if target_url is None and external_link_id is None:
            return []
        query = """
            SELECT p.url AS source_page_url, rpe.first_seen_at AS first_seen_at
            FROM run_page_external_links rpe
            JOIN pages p ON p.id = rpe.page_id
            WHERE rpe.run_id = ?
        """
        params: list[object] = [run_id]
        if external_link_id is not None:
            query += " AND rpe.external_link_id = ?"
            params.append(external_link_id)
        else:
            query += """
                AND rpe.external_link_id = (
                    SELECT id FROM external_links WHERE target_url = ? LIMIT 1
                )
            """
            params.append(target_url)
        query += " ORDER BY p.url ASC LIMIT ?"
        params.append(limit)
        rows = self._connection.execute(query, tuple(params)).fetchall()
        return [
            ExternalLinkSourcePageRecord(
                source_page_url=str(row["source_page_url"]),
                first_seen_at=str(row["first_seen_at"]),
            )
            for row in rows
        ]

    def list_page_content_metrics(
        self,
        run_id: int,
        *,
        only_significant: bool = False,
        limit: int = 200,
    ) -> list[PageContentMetricRecord]:
        query = """
            SELECT
                p.url AS url,
                rp.text_similarity_prev AS text_similarity_prev,
                rp.text_change_percent_prev AS text_change_percent_prev,
                rp.text_compared_to_run_id AS text_compared_to_run_id,
                rp.text_significant_change AS text_significant_change
            FROM run_pages rp
            JOIN pages p ON p.id = rp.page_id
            WHERE rp.run_id = ?
              AND (rp.text_change_percent_prev IS NOT NULL OR rp.text_similarity_prev IS NOT NULL)
        """
        params: list[object] = [run_id]
        if only_significant:
            query += " AND rp.text_significant_change = 1"
        query += """
            ORDER BY
                CASE WHEN rp.text_change_percent_prev IS NULL THEN 1 ELSE 0 END,
                rp.text_change_percent_prev DESC,
                p.url ASC
            LIMIT ?
        """
        params.append(limit)
        rows = self._connection.execute(query, tuple(params)).fetchall()
        out: list[PageContentMetricRecord] = []
        for row in rows:
            sig = row["text_significant_change"]
            out.append(
                PageContentMetricRecord(
                    url=str(row["url"]),
                    text_similarity_prev=float(row["text_similarity_prev"])
                    if row["text_similarity_prev"] is not None
                    else None,
                    text_change_percent_prev=float(row["text_change_percent_prev"])
                    if row["text_change_percent_prev"] is not None
                    else None,
                    text_compared_to_run_id=int(row["text_compared_to_run_id"])
                    if row["text_compared_to_run_id"] is not None
                    else None,
                    text_significant_change=bool(sig) if sig is not None else None,
                )
            )
        return out

    def list_pages_with_text(
        self,
        run_id: int,
        *,
        search: str | None = None,
        limit: int = 100,
    ) -> list[PageTextRecord]:
        query = """
            SELECT
                rp.run_id AS run_id,
                p.url AS url,
                rp.created_at AS created_at,
                LENGTH(rp.main_text) AS text_len,
                rp.main_text AS main_text
            FROM run_pages rp
            JOIN pages p ON p.id = rp.page_id
            WHERE rp.run_id = ?
              AND rp.main_text IS NOT NULL
              AND TRIM(rp.main_text) != ''
        """
        params: list[object] = [run_id]
        if search:
            query += " AND p.url LIKE ?"
            params.append(f"%{search}%")
        query += " ORDER BY rp.created_at DESC, p.url ASC LIMIT ?"
        params.append(limit)
        rows = self._connection.execute(query, tuple(params)).fetchall()
        return [
            PageTextRecord(
                run_id=int(row["run_id"]),
                url=str(row["url"]),
                created_at=str(row["created_at"]),
                text_len=int(row["text_len"]) if row["text_len"] is not None else 0,
                main_text=str(row["main_text"]),
            )
            for row in rows
        ]

    def get_page_text(self, run_id: int, url: str) -> PageTextRecord | None:
        row = self._connection.execute(
            """
            SELECT
                rp.run_id AS run_id,
                p.url AS url,
                rp.created_at AS created_at,
                LENGTH(rp.main_text) AS text_len,
                rp.main_text AS main_text
            FROM run_pages rp
            JOIN pages p ON p.id = rp.page_id
            WHERE rp.run_id = ?
              AND p.url = ?
              AND rp.main_text IS NOT NULL
              AND TRIM(rp.main_text) != ''
            LIMIT 1
            """,
            (run_id, url),
        ).fetchone()
        if row is None:
            return None
        return PageTextRecord(
            run_id=int(row["run_id"]),
            url=str(row["url"]),
            created_at=str(row["created_at"]),
            text_len=int(row["text_len"]) if row["text_len"] is not None else 0,
            main_text=str(row["main_text"]),
        )

    def compute_page_text_metrics(
        self,
        job_id: str,
        run_id: int,
        *,
        significant_change_threshold_percent: float,
        text_compare_max_chars: int,
    ) -> None:
        """Compare each page's main_text to the previous finished run; set run_pages metric columns."""
        self._connection.execute(
            """
            UPDATE run_pages
            SET
                text_similarity_prev = NULL,
                text_change_percent_prev = NULL,
                text_compared_to_run_id = NULL,
                text_significant_change = NULL
            WHERE run_id = ?
            """,
            (run_id,),
        )
        prev_id = self.get_previous_run_id(job_id=job_id, run_id=run_id)
        if prev_id is None:
            self._connection.commit()
            return

        rows = self._connection.execute(
            """
            SELECT page_id, main_text
            FROM run_pages
            WHERE run_id = ? AND main_text IS NOT NULL AND TRIM(main_text) != ''
            """,
            (run_id,),
        ).fetchall()

        for row in rows:
            page_id = int(row["page_id"])
            cur_text = str(row["main_text"])
            prev_row = self._connection.execute(
                """
                SELECT main_text
                FROM run_pages
                WHERE run_id = ? AND page_id = ?
                """,
                (prev_id, page_id),
            ).fetchone()
            if prev_row is None or prev_row["main_text"] is None:
                continue
            prev_text = str(prev_row["main_text"]).strip()
            if not prev_text:
                continue
            similarity, change_percent = main_text_similarity_and_change_percent(
                cur_text,
                prev_text,
                max_chars=text_compare_max_chars,
            )
            significant = 1 if change_percent >= significant_change_threshold_percent else 0
            self._connection.execute(
                """
                UPDATE run_pages
                SET
                    text_similarity_prev = ?,
                    text_change_percent_prev = ?,
                    text_compared_to_run_id = ?,
                    text_significant_change = ?
                WHERE run_id = ? AND page_id = ?
                """,
                (similarity, change_percent, prev_id, significant, run_id, page_id),
            )
        self._connection.commit()

    def list_run_history(self, job_id: str, limit: int = 20) -> list[CrawlRunRecord]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                started_at,
                finished_at,
                pages_visited,
                pages_failed,
                links_discovered
            FROM crawl_runs
            WHERE job_id = ?
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (job_id, limit),
        ).fetchall()
        return [
            CrawlRunRecord(
                run_id=int(row["id"]),
                started_at=str(row["started_at"]),
                finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
                pages_visited=int(row["pages_visited"]),
                pages_failed=int(row["pages_failed"]),
                links_discovered=int(row["links_discovered"]),
            )
            for row in rows
        ]

    def get_run_record(self, *, job_id: str, run_id: int) -> CrawlRunRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                started_at,
                finished_at,
                pages_visited,
                pages_failed,
                links_discovered
            FROM crawl_runs
            WHERE job_id = ? AND id = ?
            LIMIT 1
            """,
            (job_id, run_id),
        ).fetchone()
        if row is None:
            return None
        return CrawlRunRecord(
            run_id=int(row["id"]),
            started_at=str(row["started_at"]),
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
            pages_visited=int(row["pages_visited"]),
            pages_failed=int(row["pages_failed"]),
            links_discovered=int(row["links_discovered"]),
        )

    def list_crawled_pages(
        self,
        run_id: int,
        search: str | None = None,
        limit: int = 100,
        only_failed: bool = False,
        max_depth: int | None = None,
        status_code_filter: int | None = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> list[CrawlPageRecord]:
        query = """
            SELECT
                rp.run_id AS run_id,
                p.url AS url,
                rp.depth AS depth,
                rp.status_code AS status_code,
                rp.ok AS ok,
                rp.created_at AS created_at,
                rp.error_message AS error_message
            FROM run_pages rp
            JOIN pages p ON p.id = rp.page_id
            WHERE rp.run_id = ?
        """
        params: list[object] = [run_id]
        if search:
            query += " AND url LIKE ?"
            params.append(f"%{search}%")
        if only_failed:
            query += " AND ok = 0"
        if max_depth is not None:
            query += " AND depth <= ?"
            params.append(max_depth)
        if status_code_filter is not None:
            query += " AND status_code = ?"
            params.append(status_code_filter)

        order_sql = {
            "created_at": "rp.created_at DESC" if sort_desc else "rp.created_at ASC",
            "url": "url DESC" if sort_desc else "url ASC",
            "depth": "depth DESC, rp.created_at DESC" if sort_desc else "depth ASC, rp.created_at ASC",
            "status_code": (
                "(status_code IS NULL), status_code DESC, rp.created_at DESC"
                if sort_desc
                else "(status_code IS NULL) DESC, status_code ASC, rp.created_at ASC"
            ),
            "run_id": "rp.run_id DESC, rp.created_at DESC" if sort_desc else "rp.run_id ASC, rp.created_at ASC",
        }.get(sort_by, "rp.created_at DESC")
        query += f" ORDER BY {order_sql} LIMIT ?"
        params.append(limit)

        rows = self._connection.execute(query, tuple(params)).fetchall()
        return [
            CrawlPageRecord(
                run_id=int(row["run_id"]),
                url=str(row["url"]),
                depth=int(row["depth"]),
                status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                ok=bool(row["ok"]),
                created_at=str(row["created_at"]),
                error_message=str(row["error_message"]) if row["error_message"] is not None else None,
            )
            for row in rows
        ]

    def list_external_links(
        self,
        run_id: int,
        search: str | None = None,
        limit: int = 100,
        sort_by: str = "seen_count",
        sort_desc: bool = True,
    ) -> list[ExternalLinkRecord]:
        query = """
            SELECT
                el.target_url AS target_url,
                rel.first_seen_at AS first_seen_at,
                rel.seen_count AS seen_count
            FROM run_external_links rel
            JOIN external_links el ON el.id = rel.external_link_id
            WHERE rel.run_id = ?
        """
        params: list[object] = [run_id]
        if search:
            query += " AND target_url LIKE ?"
            params.append(f"%{search}%")
        order_sql = {
            "seen_count": "seen_count DESC, target_url ASC" if sort_desc else "seen_count ASC, target_url ASC",
            "target_url": "target_url DESC" if sort_desc else "target_url ASC",
            "url": "target_url DESC" if sort_desc else "target_url ASC",
            "first_seen_at": "first_seen_at DESC, target_url ASC" if sort_desc else "first_seen_at ASC, target_url ASC",
            "created_at": "first_seen_at DESC, target_url ASC" if sort_desc else "first_seen_at ASC, target_url ASC",
        }.get(sort_by, "seen_count DESC, target_url ASC")
        query += f" ORDER BY {order_sql} LIMIT ?"
        params.append(limit)

        rows = self._connection.execute(query, tuple(params)).fetchall()
        return [
            ExternalLinkRecord(
                target_url=str(row["target_url"]),
                first_seen_at=str(row["first_seen_at"]),
                seen_count=int(row["seen_count"]),
            )
            for row in rows
        ]

    def list_page_external_link_counts(self, run_id: int, *, limit: int = 5000) -> list[PageExternalCountRecord]:
        rows = self._connection.execute(
            """
            SELECT
                p.url AS url,
                rp.depth AS depth,
                rp.status_code AS status_code,
                rp.ok AS ok,
                COUNT(rpe.external_link_id) AS external_count
            FROM run_pages rp
            JOIN pages p ON p.id = rp.page_id
            LEFT JOIN run_page_external_links rpe
                ON rpe.run_id = rp.run_id
               AND rpe.page_id = rp.page_id
            WHERE rp.run_id = ?
            GROUP BY p.url, rp.depth, rp.status_code, rp.ok
            ORDER BY p.url ASC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        return [
            PageExternalCountRecord(
                url=str(row["url"]),
                depth=int(row["depth"]),
                status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                ok=bool(row["ok"]),
                external_count=int(row["external_count"] or 0),
            )
            for row in rows
        ]

    def get_table_counts(self) -> dict[str, int]:
        tables = (
            "crawl_runs",
            "crawl_pages",
            "crawl_links",
            "link_check_results",
            "pages",
            "external_links",
            "run_pages",
            "run_external_links",
            "run_pages_appeared",
            "run_pages_disappeared",
            "run_external_links_appeared",
            "run_external_links_disappeared",
            "run_page_external_links",
            "link_ignore_rules",
            "link_failure_state",
            "link_alerts",
            "link_check_screenshots",
        )
        counts: dict[str, int] = {}
        for table in tables:
            row = self._connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[table] = int(row["count"]) if row else 0
        return counts

    def get_distinct_link_counts(self) -> dict[str, int]:
        """Distinct target URLs by internal vs external (across all runs in this DB)."""
        ext = self._connection.execute("SELECT COUNT(*) AS n FROM external_links").fetchone()
        internal = self._connection.execute("SELECT COUNT(*) AS n FROM pages").fetchone()
        return {
            "external_urls_distinct": int(ext["n"]) if ext else 0,
            "internal_urls_distinct": int(internal["n"]) if internal else 0,
        }

    def compute_run_diffs(self, job_id: str, run_id: int) -> None:
        previous_run_id = self.get_previous_run_id(job_id=job_id, run_id=run_id)
        self._connection.execute("DELETE FROM run_pages_appeared WHERE run_id = ?", (run_id,))
        self._connection.execute("DELETE FROM run_pages_disappeared WHERE run_id = ?", (run_id,))
        self._connection.execute("DELETE FROM run_external_links_appeared WHERE run_id = ?", (run_id,))
        self._connection.execute("DELETE FROM run_external_links_disappeared WHERE run_id = ?", (run_id,))

        if previous_run_id is None:
            self._connection.execute(
                """
                INSERT INTO run_pages_appeared(run_id, compared_to_run_id, page_id)
                SELECT ?, NULL, page_id
                FROM run_pages
                WHERE run_id = ?
                """,
                (run_id, run_id),
            )
            self._connection.execute(
                """
                INSERT INTO run_external_links_appeared(run_id, compared_to_run_id, external_link_id)
                SELECT ?, NULL, external_link_id
                FROM run_external_links
                WHERE run_id = ?
                """,
                (run_id, run_id),
            )
            self._connection.commit()
            return

        self._connection.execute(
            """
            INSERT INTO run_pages_appeared(run_id, compared_to_run_id, page_id)
            SELECT ?, ?, cur.page_id
            FROM run_pages cur
            LEFT JOIN run_pages prev
                ON prev.run_id = ? AND prev.page_id = cur.page_id
            WHERE cur.run_id = ? AND prev.page_id IS NULL
            """,
            (run_id, previous_run_id, previous_run_id, run_id),
        )
        self._connection.execute(
            """
            INSERT INTO run_pages_disappeared(run_id, compared_to_run_id, page_id)
            SELECT ?, ?, prev.page_id
            FROM run_pages prev
            LEFT JOIN run_pages cur
                ON cur.run_id = ? AND cur.page_id = prev.page_id
            WHERE prev.run_id = ? AND cur.page_id IS NULL
            """,
            (run_id, previous_run_id, run_id, previous_run_id),
        )
        self._connection.execute(
            """
            INSERT INTO run_external_links_appeared(run_id, compared_to_run_id, external_link_id)
            SELECT ?, ?, cur.external_link_id
            FROM run_external_links cur
            LEFT JOIN run_external_links prev
                ON prev.run_id = ? AND prev.external_link_id = cur.external_link_id
            WHERE cur.run_id = ? AND prev.external_link_id IS NULL
            """,
            (run_id, previous_run_id, previous_run_id, run_id),
        )
        self._connection.execute(
            """
            INSERT INTO run_external_links_disappeared(run_id, compared_to_run_id, external_link_id)
            SELECT ?, ?, prev.external_link_id
            FROM run_external_links prev
            LEFT JOIN run_external_links cur
                ON cur.run_id = ? AND cur.external_link_id = prev.external_link_id
            WHERE prev.run_id = ? AND cur.external_link_id IS NULL
            """,
            (run_id, previous_run_id, run_id, previous_run_id),
        )
        self._connection.commit()

    def list_page_diffs(self, run_id: int, appeared: bool = True, limit: int = 100) -> list[PageDiffRecord]:
        table = "run_pages_appeared" if appeared else "run_pages_disappeared"
        rows = self._connection.execute(
            f"""
            SELECT d.run_id, d.compared_to_run_id, p.url, d.created_at
            FROM {table} d
            JOIN pages p ON p.id = d.page_id
            WHERE d.run_id = ?
            ORDER BY p.url ASC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        return [
            PageDiffRecord(
                run_id=int(row["run_id"]),
                compared_to_run_id=int(row["compared_to_run_id"]) if row["compared_to_run_id"] is not None else None,
                url=str(row["url"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def list_external_link_diffs(self, run_id: int, appeared: bool = True, limit: int = 100) -> list[ExternalLinkDiffRecord]:
        table = "run_external_links_appeared" if appeared else "run_external_links_disappeared"
        rows = self._connection.execute(
            f"""
            SELECT d.run_id, d.compared_to_run_id, el.target_url, d.created_at
            FROM {table} d
            JOIN external_links el ON el.id = d.external_link_id
            WHERE d.run_id = ?
            ORDER BY el.target_url ASC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        return [
            ExternalLinkDiffRecord(
                run_id=int(row["run_id"]),
                compared_to_run_id=int(row["compared_to_run_id"]) if row["compared_to_run_id"] is not None else None,
                target_url=str(row["target_url"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def list_crawl_runs_for_purge(
        self,
        *,
        job_id: str,
        run_id: int,
        and_older: bool,
    ) -> list[CrawlRunRecord]:
        """Return crawl runs targeted by a purge invocation, ordered by id ASC."""
        if and_older:
            query = """
                SELECT id, started_at, finished_at, pages_visited, pages_failed, links_discovered
                FROM crawl_runs
                WHERE job_id = ? AND id <= ?
                ORDER BY id ASC
            """
        else:
            query = """
                SELECT id, started_at, finished_at, pages_visited, pages_failed, links_discovered
                FROM crawl_runs
                WHERE job_id = ? AND id = ?
                ORDER BY id ASC
            """
        rows = self._connection.execute(query, (job_id, run_id)).fetchall()
        return [
            CrawlRunRecord(
                run_id=int(row["id"]),
                started_at=str(row["started_at"]),
                finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
                pages_visited=int(row["pages_visited"]),
                pages_failed=int(row["pages_failed"]),
                links_discovered=int(row["links_discovered"]),
            )
            for row in rows
        ]

    def list_link_check_runs_for_purge(
        self,
        *,
        job_id: str,
        run_id: int,
        and_older: bool,
    ) -> list[LinkCheckRunRecord]:
        """Return link-check runs targeted by a purge invocation, ordered by id ASC."""
        if and_older:
            query = """
                SELECT
                    id, job_id, based_on_crawl_run_id, started_at, finished_at,
                    checked_total, passed_total, failed_total, errored_total, ignored_total
                FROM link_check_runs
                WHERE job_id = ? AND id <= ?
                ORDER BY id ASC
            """
        else:
            query = """
                SELECT
                    id, job_id, based_on_crawl_run_id, started_at, finished_at,
                    checked_total, passed_total, failed_total, errored_total, ignored_total
                FROM link_check_runs
                WHERE job_id = ? AND id = ?
                ORDER BY id ASC
            """
        rows = self._connection.execute(query, (job_id, run_id)).fetchall()
        return [
            LinkCheckRunRecord(
                run_id=int(row["id"]),
                job_id=str(row["job_id"]),
                based_on_crawl_run_id=int(row["based_on_crawl_run_id"]),
                started_at=str(row["started_at"]),
                finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
                checked_total=int(row["checked_total"] or 0),
                passed_total=int(row["passed_total"] or 0),
                failed_total=int(row["failed_total"] or 0),
                errored_total=int(row["errored_total"] or 0),
                ignored_total=int(row["ignored_total"] or 0),
            )
            for row in rows
        ]

    def get_purge_preview_counts_crawl(self, run_ids: list[int]) -> dict[str, int]:
        """Count rows that will cascade-delete when the given crawl_runs are removed."""
        empty = {
            "crawl_runs": 0,
            "crawl_pages": 0,
            "crawl_links": 0,
            "run_pages": 0,
            "run_external_links": 0,
            "run_page_external_links": 0,
            "link_check_runs": 0,
            "link_check_results": 0,
            "link_check_screenshots": 0,
        }
        if not run_ids:
            return empty
        placeholders = ", ".join(["?"] * len(run_ids))
        params = tuple(run_ids)
        counts = dict(empty)
        counts["crawl_runs"] = len(run_ids)
        for table in ("crawl_pages", "crawl_links", "run_pages", "run_external_links", "run_page_external_links"):
            row = self._connection.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE run_id IN ({placeholders})",
                params,
            ).fetchone()
            counts[table] = int(row["n"]) if row else 0
        row = self._connection.execute(
            f"SELECT COUNT(*) AS n FROM link_check_runs WHERE based_on_crawl_run_id IN ({placeholders})",
            params,
        ).fetchone()
        counts["link_check_runs"] = int(row["n"]) if row else 0
        row = self._connection.execute(
            f"SELECT COUNT(*) AS n FROM link_check_results WHERE crawl_run_id IN ({placeholders})",
            params,
        ).fetchone()
        counts["link_check_results"] = int(row["n"]) if row else 0
        row = self._connection.execute(
            f"SELECT COUNT(*) AS n FROM link_check_screenshots WHERE crawl_run_id IN ({placeholders})",
            params,
        ).fetchone()
        counts["link_check_screenshots"] = int(row["n"]) if row else 0
        return counts

    def get_purge_preview_counts_link_check(self, lc_run_ids: list[int]) -> dict[str, int]:
        """Count rows that will cascade-delete when the given link_check_runs are removed."""
        empty = {
            "link_check_runs": 0,
            "link_check_results": 0,
            "link_check_screenshots": 0,
        }
        if not lc_run_ids:
            return empty
        placeholders = ", ".join(["?"] * len(lc_run_ids))
        params = tuple(lc_run_ids)
        counts = dict(empty)
        counts["link_check_runs"] = len(lc_run_ids)
        row = self._connection.execute(
            f"SELECT COUNT(*) AS n FROM link_check_results WHERE link_check_run_id IN ({placeholders})",
            params,
        ).fetchone()
        counts["link_check_results"] = int(row["n"]) if row else 0
        row = self._connection.execute(
            f"SELECT COUNT(*) AS n FROM link_check_screenshots WHERE link_check_run_id IN ({placeholders})",
            params,
        ).fetchone()
        counts["link_check_screenshots"] = int(row["n"]) if row else 0
        return counts

    def list_artifact_files_for_crawl_runs(self, run_ids: list[int]) -> list[str]:
        """Return artifact filenames that will be orphaned when the given crawl_runs are removed."""
        if not run_ids:
            return []
        placeholders = ", ".join(["?"] * len(run_ids))
        rows = self._connection.execute(
            f"""
            SELECT artifact_file
            FROM link_check_screenshots
            WHERE crawl_run_id IN ({placeholders})
              AND artifact_file IS NOT NULL
              AND TRIM(artifact_file) != ''
            """,
            tuple(run_ids),
        ).fetchall()
        return [str(row["artifact_file"]) for row in rows]

    def list_artifact_files_for_link_check_runs(self, lc_run_ids: list[int]) -> list[str]:
        """Return artifact filenames that will be orphaned when the given link_check_runs are removed."""
        if not lc_run_ids:
            return []
        placeholders = ", ".join(["?"] * len(lc_run_ids))
        rows = self._connection.execute(
            f"""
            SELECT artifact_file
            FROM link_check_screenshots
            WHERE link_check_run_id IN ({placeholders})
              AND artifact_file IS NOT NULL
              AND TRIM(artifact_file) != ''
            """,
            tuple(lc_run_ids),
        ).fetchall()
        return [str(row["artifact_file"]) for row in rows]

    def count_link_alerts_referencing_runs(self, *, job_id: str, run_ids: list[int]) -> int:
        """Count link_alerts whose last_reported_run_id points at any of these crawl runs."""
        if not run_ids:
            return 0
        placeholders = ", ".join(["?"] * len(run_ids))
        row = self._connection.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM link_alerts
            WHERE job_id = ?
              AND last_reported_run_id IN ({placeholders})
            """,
            (job_id, *run_ids),
        ).fetchone()
        return int(row["n"]) if row else 0

    def null_link_alert_last_run_for_runs(self, *, job_id: str, run_ids: list[int]) -> int:
        """NULL out link_alerts.last_reported_run_id for stale references to deleted runs."""
        if not run_ids:
            return 0
        placeholders = ", ".join(["?"] * len(run_ids))
        cursor = self._connection.execute(
            f"""
            UPDATE link_alerts
            SET last_reported_run_id = NULL
            WHERE job_id = ?
              AND last_reported_run_id IN ({placeholders})
            """,
            (job_id, *run_ids),
        )
        self._connection.commit()
        return int(cursor.rowcount or 0)

    def delete_crawl_runs(self, run_ids: list[int]) -> None:
        """Delete crawl runs by id; relies on PRAGMA foreign_keys=ON for cascades."""
        if not run_ids:
            return
        self._connection.execute("PRAGMA foreign_keys = ON")
        placeholders = ", ".join(["?"] * len(run_ids))
        self._connection.execute(
            f"DELETE FROM crawl_runs WHERE id IN ({placeholders})",
            tuple(run_ids),
        )
        self._connection.commit()

    def delete_link_check_runs(self, lc_run_ids: list[int]) -> None:
        """Delete link-check runs by id; relies on PRAGMA foreign_keys=ON for cascades."""
        if not lc_run_ids:
            return
        self._connection.execute("PRAGMA foreign_keys = ON")
        placeholders = ", ".join(["?"] * len(lc_run_ids))
        self._connection.execute(
            f"DELETE FROM link_check_runs WHERE id IN ({placeholders})",
            tuple(lc_run_ids),
        )
        self._connection.commit()
