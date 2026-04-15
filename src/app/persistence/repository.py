"""Repository layer for crawl persistence."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

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
class PageContentMetricRecord:
    url: str
    text_similarity_prev: float | None
    text_change_percent_prev: float | None
    text_compared_to_run_id: int | None
    text_significant_change: bool | None


@dataclass(frozen=True)
class LinkCheckResultRecord:
    row_id: int
    crawl_link_id: int
    crawl_run_id: int
    target_url: str
    status_code: int | None
    ok: bool
    error_message: str | None
    checked_at: str


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

    def add_link(self, run_id: int, source_url: str, target_url: str, is_internal: bool) -> None:
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
                    VALUES (?, ?, ?, NULL)
                    ON CONFLICT(run_id, page_id, external_link_id) DO NOTHING
                    """,
                    (run_id, page_id, external_link_id),
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
        target_url: str,
        status_code: int | None,
        ok: bool,
        error_message: str | None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO link_check_results(
                crawl_link_id,
                crawl_run_id,
                target_url,
                status_code,
                ok,
                error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (crawl_link_id, crawl_run_id, target_url, status_code, int(ok), error_message),
        )
        self._connection.commit()

    def list_latest_link_check_results(
        self,
        crawl_run_id: int,
        limit: int | None = None,
    ) -> list[LinkCheckResultRecord]:
        query = """
            SELECT
                l.id,
                l.crawl_link_id,
                l.crawl_run_id,
                l.target_url,
                l.status_code,
                l.ok,
                l.error_message,
                l.checked_at
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
                target_url=str(row["target_url"]),
                status_code=int(row["status_code"]) if row["status_code"] is not None else None,
                ok=bool(row["ok"]),
                error_message=str(row["error_message"]) if row["error_message"] is not None else None,
                checked_at=str(row["checked_at"]),
            )
            for row in rows
        ]

    def list_source_pages_for_targets(self, run_id: int, target_urls: list[str]) -> dict[str, list[str]]:
        if not target_urls:
            return {}
        placeholders = ", ".join(["?"] * len(target_urls))
        rows = self._connection.execute(
            f"""
            SELECT el.target_url AS target_url, p.url AS source_page_url
            FROM run_page_external_links rpe
            JOIN pages p ON p.id = rpe.page_id
            JOIN external_links el ON el.id = rpe.external_link_id
            WHERE rpe.run_id = ? AND el.target_url IN ({placeholders})
            ORDER BY el.target_url ASC, p.url ASC
            """,
            (run_id, *target_urls),
        ).fetchall()
        mapped: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            mapped[str(row["target_url"])].append(str(row["source_page_url"]))
        return dict(mapped)

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
