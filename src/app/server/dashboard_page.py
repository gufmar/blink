"""HTML for scheduler/results dashboards (proxy-safe and route-aware)."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

_LINK_CHECK_CHILD_MARKER = "\u2b11"  # ↫ LEFTWARDS ARROW WITH TIP UPWARDS


def _fmt_dt_short(value: object, *, ongoing: bool = False) -> str:
    if value is None or str(value).strip() == "":
        return "ongoing" if ongoing else "—"
    raw = str(value).strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _html_action_link(url: object, label: str) -> str:
    href = str(url or "").strip()
    if not href:
        return "—"
    return f'<a href="{html.escape(href)}">{html.escape(label)}</a>'


def _html_log_links_cell(run: dict[str, Any]) -> str:
    view_url = str(run.get("logs_view_url") or "").strip()
    if view_url:
        return _html_action_link(view_url, "logs")
    pairs = list(run.get("log_links") or [])
    if not pairs:
        return "—"
    return " ".join(
        f'<a href="{html.escape(str(url))}">{html.escape(str(d))}</a>' for url, d in pairs
    )


def _auth_nav_html(links: dict[str, str]) -> str:
    user = str(links.get("auth_user") or "").strip()
    logout = str(links.get("auth_logout") or "").strip()
    if not user and not logout:
        return ""
    logout_link = f' · <a href="{html.escape(logout)}">Log out</a>' if logout else ""
    return f'<span class="auth-user">Signed in as {html.escape(user)}</span>{logout_link}'


def _header_nav_links(links: dict[str, str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    dash = str(links.get("main_dashboard") or "").strip()
    if dash:
        out.append(("< Dashboard", dash))
    job_url = str(links.get("job") or "").strip()
    if job_url and str(links.get("nav_job_id") or "").strip():
        out.append(("< job", job_url))
    return out


def _render_header_nav(links: dict[str, str]) -> str:
    nav_html = "\n".join(f'<a href="{esc(href)}">{esc(label)}</a>' for label, href in _header_nav_links(links) if href)
    auth_html = _auth_nav_html(links)
    if auth_html:
        nav_html = f"{nav_html}\n{auth_html}" if nav_html else auth_html
    return nav_html


def _footer_links_html(
    links: dict[str, str],
    *,
    gen_at: str,
    extra: tuple[str, str] | None = None,
) -> str:
    parts = [f"Generated {esc(gen_at)}"]
    runtime = str(links.get("admin_runtime") or "").strip()
    if runtime:
        parts.append(f'<a href="{esc(runtime)}">Runtime</a>')
    if extra:
        href, label = extra
        if str(href or "").strip() and label:
            parts.append(f'<a href="{esc(href)}">{esc(label)}</a>')
    return " · ".join(parts)


def _html_report_link_cell(run: dict[str, Any]) -> str:
    view_url = str(run.get("json_view_url") or run.get("report_url") or "").strip()
    if not view_url:
        return "—"
    return _html_action_link(view_url, "json")


def render_admin_runtime_html(*, diagnostics: dict[str, Any], links: dict[str, str]) -> str:
    """Admin-only page for paths, scheduler runtime, and environment variables."""

    def row_cells(label: str, value: object) -> str:
        return f"<tr><td>{esc(label)}</td><td class=\"mono\">{esc(value)}</td></tr>"

    empty_row = '<tr><td colspan="2" class="empty">—</td></tr>'
    path_rows = "".join(row_cells(k, v) for k, v in list(diagnostics.get("paths") or [])) or empty_row
    service_rows = "".join(row_cells(k, v) for k, v in list(diagnostics.get("service") or [])) or empty_row
    env_rows_list: list[str] = []
    for row in list(diagnostics.get("environment") or []):
        if isinstance(row, dict):
            env_rows_list.append(row_cells(str(row.get("name") or ""), row.get("value")))
    env_rows = "".join(env_rows_list) or empty_row
    ops_rows_list: list[str] = []
    for label, key in (
        ("Health", "health"),
        ("Slack health", "slack_health"),
        ("Schedule JSON", "schedule_json"),
        ("Jobs JSON", "jobs_json"),
    ):
        url = str(links.get(key) or "").strip()
        if url:
            ops_rows_list.append(
                f'<tr><td>{esc(label)}</td><td><a href="{esc(url)}">{esc(url)}</a></td></tr>'
            )
    ops_rows = "".join(ops_rows_list) or empty_row
    body_extra = f"""
<section class="panel" aria-label="Operations">
  <div class="panel-head">Blink operations</div>
  <table><tbody>{ops_rows}</tbody></table>
</section>
<section class="panel" aria-label="Paths">
  <div class="panel-head">Paths</div>
  <table><tbody>{path_rows}</tbody></table>
</section>
<section class="panel" aria-label="Service">
  <div class="panel-head">Service runtime</div>
  <table><tbody>{service_rows}</tbody></table>
</section>
<section class="panel" aria-label="Environment">
  <div class="panel-head">Environment variables</div>
  <table><tbody>{env_rows}</tbody></table>
</section>
"""
    return _render_results_shell(
        title="Blink · Runtime",
        heading="Runtime & environment",
        subtitle="Admin diagnostics: paths, scheduler state, and masked environment values.",
        panel_title="",
        table_headers=[],
        table_rows="",
        body_extra=body_extra,
        show_table_header=False,
        branding_links=links,
    )


def render_schedule_dashboard_html(payload: dict[str, Any], *, links: dict[str, str]) -> str:
    """Build scheduler dashboard HTML using request-aware links."""
    job_count = int(payload.get("job_count") or 0)
    crawl_n = int(payload.get("scheduled_crawl_count", len(payload.get("crawl_tasks") or [])))
    link_n = int(payload.get("scheduled_link_check_count", len(payload.get("link_check_tasks") or [])))
    scheduler_on = bool(payload.get("scheduler_running"))

    def esc(s: object) -> str:
        return html.escape("" if s is None else str(s))

    def _fmt_dt_short(value: object) -> str:
        if value is None or str(value).strip() == "":
            return "—"
        raw = str(value).strip()
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return raw
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.strftime("%Y-%m-%d %H:%M")

    def _fmt_from_now(value: object) -> str:
        if value is None or str(value).strip() == "":
            return "—"
        raw = str(value).strip()
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return "—"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        now = datetime.now(tz=UTC)
        delta_seconds = int((parsed - now).total_seconds())
        if delta_seconds <= 0:
            return "due now"
        hours = delta_seconds // 3600
        minutes = (delta_seconds % 3600) // 60
        return f"{hours}:{minutes:02d} from now"

    def _resolve_last_runtime(
        task: dict[str, Any] | None,
        latest: dict[str, Any] | None,
        *,
        kind: str,
    ) -> dict[str, Any]:
        rt = dict((task or {}).get("runtime") or {})
        if rt.get("last_end_at"):
            return rt
        if not latest:
            return rt
        finished = latest.get("finished_at")
        if not finished:
            return {**rt, "running": True}
        if kind == "crawl":
            exit_code = 0 if int(latest.get("pages_failed") or 0) == 0 else 1
        else:
            exit_code = 0 if int(latest.get("failed_total") or 0) == 0 else 1
        return {
            **rt,
            "last_end_at": finished,
            "last_exit_code": exit_code,
            "running": False,
        }

    def _status_dot(rt: dict[str, Any]) -> str:
        running = bool(rt.get("running"))
        code = rt.get("last_exit_code")
        if running:
            return '<span class="dot dot-run" title="Running" aria-label="Running"></span>'
        if code is None:
            return '<span class="dot dot-idle" title="No status" aria-label="No status"></span>'
        if int(code) == 0:
            return '<span class="dot dot-ok" title="OK" aria-label="OK"></span>'
        return '<span class="dot dot-fail" title="Failed" aria-label="Failed"></span>'

    def _job_line(dot_html: str, label: str, when_html: str, summary_html: str) -> str:
        return (
            f'<div class="job-line-grid">'
            f'<span class="line-dot">{dot_html}</span>'
            f'<span class="line-label">{esc(label)}</span>'
            f'<span class="line-date">{when_html}</span>'
            f'<span class="line-summary">{summary_html}</span>'
            f"</div>"
        )

    def _line_last(label: str, task: dict[str, Any] | None, job_row: dict[str, Any]) -> str:
        is_crawl = label == "last crawl"
        src = task or job_row
        latest = (task or {}).get("latest_crawl" if is_crawl else "latest_link") or job_row.get(
            "latest_crawl" if is_crawl else "latest_link"
        )
        details_url = str(
            (task or {}).get("latest_crawl_url" if is_crawl else "latest_link_url")
            or job_row.get("latest_crawl_url" if is_crawl else "latest_link_url")
            or ""
        )
        rt = _resolve_last_runtime(task, latest, kind="crawl" if is_crawl else "link_check")
        when_raw = rt.get("last_end_at") or (latest or {}).get("finished_at") or (latest or {}).get("started_at")
        when = _fmt_dt_short(when_raw)
        if latest or when != "—":
            when_cell = (
                f'<a href="{esc(details_url)}" class="mono">{esc(when)}</a>'
                if details_url and when != "—"
                else f'<span class="mono">{esc(when)}</span>'
            )
        else:
            when_cell = '<span class="mono">—</span>'
        if is_crawl:
            latest_crawl = latest or {}
            summary = (
                f'pages: {esc(latest_crawl.get("pages_visited") if latest_crawl.get("pages_visited") is not None else "—")} · '
                f'ignored: {esc(src.get("ignored_total") if src.get("ignored_total") is not None else "—")} · '
                f'ext.links: {esc(src.get("external_total") if src.get("external_total") is not None else "—")}'
            )
        else:
            latest_link = latest or {}
            summary = (
                f'tested: {esc(latest_link.get("checked_total") if latest_link.get("checked_total") is not None else "—")} · '
                f'ignored: {esc(src.get("ignored_total") if src.get("ignored_total") is not None else "—")} '
                f'({esc(src.get("ignored_ratio") if src.get("ignored_ratio") is not None else 0)}%) · '
                f'failed: {esc(src.get("failed_total") if src.get("failed_total") is not None else "—")} '
                f'({esc(src.get("failed_ratio") if src.get("failed_ratio") is not None else 0)}%)'
            )
        if not latest and when == "—":
            summary = "—"
        return _job_line(_status_dot(rt), label, when_cell, f'<span class="mono">{summary}</span>')

    def _line_next(label: str, task: dict[str, Any] | None) -> str:
        dot_cell = '<span class="line-dot-spacer" aria-hidden="true"></span>'
        if not task:
            return _job_line(
                dot_cell,
                label,
                '<span class="mono line-muted">not scheduled</span>',
                '<span class="mono">—</span>',
            )
        rt = task.get("runtime") or {}
        dec = task.get("declarative") or {}
        if not dec.get("enabled"):
            return _job_line(
                dot_cell,
                label,
                '<span class="mono line-muted">not scheduled</span>',
                '<span class="mono">—</span>',
            )
        expr = str(dec.get("expression") or "").strip()
        cadence = f"{expr} cadence" if expr else "—"
        next_raw = rt.get("next_run_at")
        if not next_raw:
            return _job_line(
                dot_cell,
                label,
                '<span class="mono line-muted">not scheduled</span>',
                f'<span class="mono">{esc(cadence)}</span>',
            )
        return _job_line(
            dot_cell,
            label,
            f'<span class="mono">{esc(_fmt_dt_short(next_raw))}</span>',
            f'<span class="mono">{esc(cadence)} · {esc(_fmt_from_now(next_raw))}</span>',
        )

    def row_for(job_row: dict[str, Any]) -> str:
        job_name = str(job_row.get("job_name") or job_row.get("job_id") or "")
        history_url = str(job_row.get("history_url") or "").strip()
        history_link = (
            f' <a class="job-history-link" href="{esc(history_url)}">history</a>'
            if history_url
            else ""
        )
        crawl_task = job_row.get("crawl")
        link_task = job_row.get("link_check")
        crawl_block = (
            f'<div class="job-lines-block job-lines-crawl">'
            f'{_line_last("last crawl", crawl_task, job_row)}'
            f'{_line_next("next crawl", crawl_task)}'
            f"</div>"
        )
        check_block = (
            f'<div class="job-lines-block job-lines-check">'
            f'{_line_last("last check", link_task, job_row)}'
            f'{_line_next("next check", link_task)}'
            f"</div>"
        )
        return f"""<tr>
    <td class="job-cell">
      <div class="job-card-inner">
        <div class="job-title-row"><span class="job-title">{esc(job_name)}</span>{history_link}</div>
        {crawl_block}
        {check_block}
      </div>
    </td>
  </tr>"""

    job_rows = list(payload.get("job_rows") or [])
    rows = "\n".join(row_for(row) for row in job_rows) if job_rows else ""
    empty_row = '<tr><td class="empty">No jobs found.</td></tr>'

    gen_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    status_class = "pill pill-on" if scheduler_on else "pill pill-off"
    status_label = "Running" if scheduler_on else "Stopped"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Blink · Schedules</title>
  {_favicon_head(links)}
  <style>
    :root {{
      --cardano-blue: #121f63;
      --cardano-blue-light: #1e3a8a;
      --surface: #f4f6fb;
      --card: #ffffff;
      --text: #1e293b;
      --muted: #64748b;
      --border: #e2e8f0;
      --success: #059669;
      --danger: #dc2626;
      --warn: #d97706;
      --radius: 12px;
      --shadow: 0 4px 24px rgba(18, 31, 99, 0.08);
      --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--font);
      background: var(--surface);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
    }}
    .hero {{
      background: linear-gradient(135deg, var(--cardano-blue) 0%, var(--cardano-blue-light) 100%);
      color: #fff;
      padding: 2rem 1.5rem 2.25rem;
      box-shadow: var(--shadow);
    }}
    .hero-inner {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 0.35rem;
    }}
    .brand-logo {{
      width: 36px;
      height: 36px;
      object-fit: contain;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.14);
      padding: 4px;
    }}
    .brand-name {{
      font-size: 2.25rem;
      line-height: 36px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .brand-tagline {{
      margin: 0 0 0.75rem;
      font-size: 1.125rem;
      font-weight: 500;
      opacity: 0.92;
      letter-spacing: 0.01em;
    }}
    .job-title-row {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 1rem;
      margin-bottom: 0.2rem;
    }}
    .job-title {{
      font-size: 1rem;
      font-weight: 700;
      color: var(--cardano-blue);
    }}
    .job-history-link {{
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--cardano-blue-light);
      text-decoration: underline;
      text-underline-offset: 2px;
      white-space: nowrap;
    }}
    .job-lines-block {{
      display: flex;
      flex-direction: column;
      gap: 0.08rem;
    }}
    .job-lines-crawl {{
      margin-top: 0.15rem;
    }}
    .job-lines-check {{
      margin-top: 0.65rem;
    }}
    .job-line-grid {{
      display: grid;
      grid-template-columns: 1.1rem 6.75rem 11rem minmax(13rem, 1fr);
      gap: 0.55rem 0.75rem;
      align-items: center;
      font-size: 0.82rem;
    }}
    .line-dot {{
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .line-dot-spacer {{
      display: block;
      width: 8px;
      height: 8px;
    }}
    .line-label {{
      color: var(--muted);
      font-weight: 600;
      white-space: nowrap;
    }}
    .line-date {{ white-space: nowrap; }}
    .line-summary {{ color: var(--text); }}
    .line-muted {{ color: var(--muted); }}
    .job-cell {{
      padding: 0.65rem 1rem;
      vertical-align: top;
    }}
    .job-card-inner {{
      min-width: 34rem;
    }}
    .panel-scroll {{
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      overscroll-behavior-x: contain;
    }}
    .job-table {{
      min-width: 100%;
    }}
    .hero h1 {{
      margin: 0 0 0.35rem;
      font-size: 1.65rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .hero p {{
      margin: 0;
      opacity: 0.92;
      font-size: 0.95rem;
      max-width: 42rem;
    }}
    .nav {{
      margin-top: 1rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 1rem;
      font-size: 0.875rem;
    }}
    .nav a {{
      color: #fff;
      opacity: 0.95;
      text-decoration: underline;
      text-underline-offset: 3px;
    }}
    .nav a:hover {{ opacity: 1; }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 1.5rem;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .metric {{
      background: var(--card);
      border-radius: var(--radius);
      padding: 1.15rem 1.25rem;
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
    }}
    .metric .value {{
      font-size: 1.75rem;
      font-weight: 700;
      color: var(--cardano-blue);
      letter-spacing: -0.03em;
    }}
    .metric .label {{
      font-size: 0.8rem;
      color: var(--muted);
      margin-top: 0.25rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .metric .hint {{
      font-size: 0.75rem;
      color: var(--muted);
      margin-top: 0.35rem;
    }}
    .pill {{
      display: inline-block;
      padding: 0.2rem 0.65rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .pill-on {{ background: rgba(5, 150, 105, 0.15); color: var(--success); }}
    .pill-off {{ background: rgba(220, 38, 38, 0.12); color: var(--danger); }}
    .panel {{
      background: var(--card);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
      overflow: hidden;
    }}
    .panel-head {{
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--border);
      font-weight: 600;
      font-size: 1rem;
      color: var(--cardano-blue);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8125rem;
    }}
    th {{
      text-align: left;
      padding: 0.65rem 1rem;
      background: #f8fafc;
      color: var(--muted);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      font-size: 0.7rem;
      border-bottom: 1px solid var(--border);
    }}
    td {{
      padding: 0.65rem 1rem;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }}
    tr:nth-child(even) td {{ background: #fafbfd; }}
    tr:last-child td {{ border-bottom: none; }}
    td.empty {{
      text-align: center;
      color: var(--muted);
      padding: 2rem;
    }}
    .mono {{
      font-family: ui-monospace, "Cascadia Code", "SF Mono", Menlo, monospace;
      font-size: 0.78rem;
    }}
    .cadence {{ max-width: 14rem; }}
    .time {{ white-space: nowrap; }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.45rem;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 600;
    }}
    .badge-crawl {{ background: rgba(30, 58, 138, 0.12); color: var(--cardano-blue-light); }}
    .badge-link_check {{ background: rgba(5, 150, 105, 0.12); color: var(--success); }}
    .dot {{
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 0.35rem;
      vertical-align: middle;
    }}
    .dot-run {{ background: var(--warn); }}
    .dot-idle {{ background: var(--muted); }}
    .dot-ok {{ background: var(--success); }}
    .dot-fail {{ background: var(--danger); }}
    .dashboard-footer {{
      margin-top: 2rem;
      padding: 1rem 0;
      font-size: 0.75rem;
      color: var(--muted);
      text-align: center;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: center;
      gap: 0.75rem 1.25rem;
    }}
    .auto-refresh-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      cursor: pointer;
      user-select: none;
    }}
    .auto-refresh-toggle input {{
      width: 1rem;
      height: 1rem;
      accent-color: var(--cardano-blue);
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      {_brand_header(links)}
      <p class="brand-tagline">handle broken links in a blink</p>
      <nav class="nav" aria-label="Navigation">
        {_render_header_nav(links)}
      </nav>
    </div>
  </header>
  <div class="wrap">
    <section class="metrics" aria-label="Summary">
      <div class="metric">
        <div class="value">{job_count}</div>
        <div class="label">jobs</div>
      </div>
      <div class="metric">
        <div class="value">{crawl_n}</div>
        <div class="label">scheduled crawls</div>
      </div>
      <div class="metric">
        <div class="value">{link_n}</div>
        <div class="label">scheduled link checks</div>
      </div>
      <div class="metric">
        <div class="value"><span class="{status_class}">{status_label}</span></div>
        <div class="label">service status</div>
      </div>
    </section>
    <section class="panel" aria-label="Task table">
      <div class="panel-head">Task overview</div>
      <div class="panel-scroll">
      <table class="job-table">
        <thead>
          <tr>
            <th>Job</th>
          </tr>
        </thead>
        <tbody>
          {rows if rows else empty_row}
        </tbody>
      </table>
      </div>
    </section>
    <footer class="dashboard-footer">
      <span class="footer-generated">{_footer_links_html(links, gen_at=gen_at)}</span>
      <label class="auto-refresh-toggle">
        <input type="checkbox" id="dashboardAutoRefresh" aria-label="Auto-refresh every 15 seconds"/>
        <span>Auto-refresh (15s)</span>
      </label>
    </footer>
<script>
(function () {{
  var STORAGE_KEY = "blink-dashboard-auto-refresh";
  var INTERVAL_MS = 15000;
  var toggle = document.getElementById("dashboardAutoRefresh");
  if (!toggle) return;
  var timer = null;
  function start() {{
    stop();
    timer = window.setInterval(function () {{ window.location.reload(); }}, INTERVAL_MS);
  }}
  function stop() {{
    if (timer !== null) {{
      window.clearInterval(timer);
      timer = null;
    }}
  }}
  try {{
    if (window.localStorage.getItem(STORAGE_KEY) === "1") {{
      toggle.checked = true;
      start();
    }}
  }} catch (e) {{}}
  toggle.addEventListener("change", function () {{
    try {{
      window.localStorage.setItem(STORAGE_KEY, toggle.checked ? "1" : "0");
    }} catch (e) {{}}
    if (toggle.checked) start(); else stop();
  }});
}})();
</script>
  </div>
</body>
</html>
"""


def _task_sort_key(t: dict[str, Any]) -> tuple[str, str, str]:
    rt = t.get("runtime") or {}
    next_at = str(rt.get("next_run_at") or "")
    return (next_at, str(t.get("job_id") or ""), str(t.get("task_type") or ""))


def _status_cell(rt: dict[str, Any]) -> str:
    running = bool(rt.get("running"))
    code = rt.get("last_exit_code")
    if running:
        return '<span class="dot dot-run"></span>Running'
    if code is None:
        return '<span class="dot dot-idle"></span>—'
    if int(code) == 0:
        return '<span class="dot dot-ok"></span>OK'
    return '<span class="dot dot-fail"></span>Failed'


def render_results_jobs_html(payload: dict[str, Any], *, links: dict[str, str]) -> str:
    """Render job-centric results index (history-first navigation)."""
    rows_data = list(payload.get("jobs_summary") or [])

    def _line(kind: str, run: dict[str, Any] | None, url: str) -> str:
        run = run or {}
        metric = run.get("pages_visited") if kind == "crawl" else run.get("checked_total")
        metric_label = "pages" if kind == "crawl" else "checked"
        run_id = run.get("run_id")
        run_cell = f'<a href="{esc(url)}" class="mono">run {esc(run_id)}</a>' if (run_id is not None and url) else "—"
        return (
            f'<div class="job-line"><span class="badge badge-{"crawl" if kind == "crawl" else "link_check"}">{esc(kind)}</span> '
            f'{run_cell} · <span class="mono">{esc(run.get("started_at") or "—")}</span> · '
            f'<span class="mono">{esc(metric if metric is not None else "—")} {esc(metric_label)}</span></div>'
        )

    def row_for(row: dict[str, Any]) -> str:
        history_url = str(row.get("crawl_history_url") or "")
        crawl_u = str(row.get("crawl_runs_url") or "")
        lc_u = str(row.get("link_check_runs_url") or "")
        list_links = ""
        if crawl_u and lc_u:
            list_links = (
                f' <span class="mono">(<a href="{esc(crawl_u)}">crawls</a>'
                f' · <a href="{esc(lc_u)}">link-checks</a>)</span>'
            )
        title = (
            f'<a href="{esc(history_url)}" class="mono">{esc(row.get("job_id", ""))}</a>{list_links} · {esc(row.get("name", ""))}'
            if history_url
            else f'{esc(row.get("job_id", ""))}{list_links} · {esc(row.get("name", ""))}'
        )
        crawl_line = _line("crawl", row.get("latest_crawl"), history_url)
        link_line = _line("link-check", row.get("latest_link_check"), str(row.get("latest_link_check_url") or ""))
        return f"""<tr>
    <td>
      <div class="job-title">{title}</div>
      {crawl_line}
      {link_line}
    </td>
  </tr>"""

    rows = "\n".join(row_for(row) for row in rows_data) if rows_data else ""
    empty_row = '<tr><td class="empty">No jobs with results found.</td></tr>'
    return _render_results_shell(
        title="Blink results · Jobs",
        heading="Blink results",
        subtitle="Job-centric view: open crawl history, then drill into related link-check runs.",
        panel_title="Job overview",
        table_headers=["Jobs"],
        table_rows=rows if rows else empty_row,
        footer_extra=(links.get("refresh", ""), "Refresh"),
        branding_links=links,
    )


def render_results_job_html(
    *,
    job: dict[str, Any],
    task_type: str,
    run_rows: list[dict[str, Any]],
    links: dict[str, str],
) -> str:
    """Render one job+task type run history table."""

    def _fmt_dt(value: object, *, ongoing: bool = False) -> str:
        if value is None or str(value).strip() == "":
            return "ongoing" if ongoing else "—"
        raw = str(value).strip()
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return raw
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.strftime("%Y-%m-%d %H:%M")

    def _with_ratio_tooltip(value: int | None, total: int, label: str) -> str:
        if value is None:
            return "—"
        if total <= 0:
            return f'<span title="{esc(label)} ratio unavailable: total external URLs is 0">{esc(value)} (0%)</span>'
        ratio = (float(value) / float(total)) * 100.0
        ratio_int = int(round(ratio))
        tooltip = f"{label} ratio: {value}/{total} = {ratio_int}%"
        return f'<span title="{esc(tooltip)}">{esc(value)} ({esc(ratio_int)}%)</span>'

    def row_for(run: dict[str, Any]) -> str:
        started = esc(_fmt_dt(run.get("started_at")))
        row_task_type = str(run.get("task_type") or task_type)
        finished_val = run.get("finished_at")
        finished = esc(_fmt_dt(finished_val, ongoing=True))
        total_external_urls = int(run.get("total_external_urls") or 0)
        run_id = run.get("run_id", "")
        log_cell = f'<td class="mono">{_html_log_links_cell(run)}</td>'
        rep_cell = f'<td class="mono">{_html_report_link_cell(run)}</td>'
        if task_type == "all":
            if row_task_type == "link_check":
                based_on = run.get("based_on_crawl_run_id")
                run_id_cell = (
                    f'<span title="{esc(f"this link-check is pased on crawl run {based_on}")}">{esc(run_id)}</span>'
                    if based_on is not None
                    else esc(run_id)
                )
                return f"""<tr>
    <td class="mono"><a href="{esc(run.get("details_url", ""))}">{started}</a></td>
    <td>{esc(row_task_type)}</td>
    <td class="mono">{finished}</td>
    <td class="mono">{run_id_cell}</td>
    <td class="mono">{esc(run.get("checked_total") if run.get("checked_total") is not None else "—")}</td>
    <td class="mono">{_with_ratio_tooltip(run.get("ignored_total"), total_external_urls, "ignored")}</td>
    <td class="mono">{_with_ratio_tooltip(run.get("failed_total"), total_external_urls, "failed")}</td>
    {log_cell}
    {rep_cell}
  </tr>"""
            return f"""<tr>
    <td class="mono"><a href="{esc(run.get("details_url", ""))}">{started}</a></td>
    <td>{esc(row_task_type)}</td>
    <td class="mono">{finished}</td>
    <td class="mono">{esc(run_id)}</td>
    <td class="mono">{esc(run.get("pages_visited") if run.get("pages_visited") is not None else "—")}</td>
    <td class="mono">—</td>
    <td class="mono">{esc(run.get("pages_failed") if run.get("pages_failed") is not None else "—")}</td>
    {log_cell}
    {rep_cell}
  </tr>"""
        if task_type == "link_check":
            based_on = run.get("based_on_crawl_run_id")
            run_id_cell = (
                f'<span title="{esc(f"this link-check is pased on crawl run {based_on}")}">{esc(run_id)}</span>'
                if based_on is not None
                else esc(run_id)
            )
            return f"""<tr>
    <td class="mono"><a href="{esc(run.get("details_url", ""))}">{started}</a></td>
    <td class="mono">{finished}</td>
    <td class="mono">{run_id_cell}</td>
    <td class="mono">{esc(run.get("checked_total") if run.get("checked_total") is not None else "—")}</td>
    <td class="mono">{_with_ratio_tooltip(run.get("ignored_total"), total_external_urls, "ignored")}</td>
    <td class="mono">{_with_ratio_tooltip(run.get("failed_total"), total_external_urls, "failed")}</td>
    {log_cell}
    {rep_cell}
  </tr>"""
        return f"""<tr>
    <td class="mono"><a href="{esc(run.get("details_url", ""))}">{started}</a></td>
    <td class="mono">{esc(run_id)}</td>
    <td class="mono">{finished}</td>
    <td class="mono">{esc(run.get("pages_visited"))}</td>
    <td class="mono">—</td>
    <td class="mono">{esc(run.get("pages_failed"))}</td>
    {log_cell}
    {rep_cell}
  </tr>"""

    rows = "\n".join(row_for(run) for run in run_rows) if run_rows else ""
    empty_colspan = "9" if task_type == "all" else "8"
    empty_row = f'<tr><td colspan="{empty_colspan}" class="empty">No runs available for this task yet.</td></tr>'
    if task_type == "link_check":
        table_headers = [
            "Checked at",
            "Finished",
            "Link-check run id",
            "Visited/Checked",
            "Ignored",
            "Failed",
            "Logs",
            "Report",
        ]
    elif task_type == "all":
        table_headers = [
            "Started",
            "Task type",
            "Finished",
            "Run id",
            "Visited/Checked",
            "Ignored",
            "Failed",
            "Logs",
            "Report",
        ]
    else:
        table_headers = [
            "Started",
            "Run id",
            "Finished",
            "Visited/Checked",
            "Ignored",
            "Failed",
            "Logs",
            "Report",
        ]
    merged_u = str(links.get("merged_runs_url") or "").strip()
    crawl_u = str(links.get("crawl_runs_url") or "").strip()
    lc_u = str(links.get("link_check_runs_url") or "").strip()
    runs_json_u = str(links.get("runs_json") or "").strip()
    mode = str(links.get("run_mode") or "")
    tabs_row = ""
    if merged_u and crawl_u and lc_u:
        def _tab_label(active: bool, label: str) -> str:
            return f"<strong>{label}</strong>" if active else label

        tabs_row = (
            '<section class="panel" aria-label="Run views" style="margin-bottom: 1rem;">'
            '<div class="panel-head">View</div>'
            '<div style="padding: 0.75rem 1rem;">'
            f'<a href="{esc(merged_u)}">{_tab_label(mode == "all", "Combined")}</a> · '
            f'<a href="{esc(crawl_u)}">{_tab_label(mode == "crawl", "Crawls")}</a> · '
            f'<a href="{esc(lc_u)}">{_tab_label(mode == "link_check", "Link checks")}</a>'
            f'{" · " + _html_action_link(runs_json_u, "runs JSON") if runs_json_u else ""}'
            "</div></section>"
        )
    toggle_row = ""
    if task_type == "all":
        show_crawl = bool(links.get("show_crawl"))
        show_link_check = bool(links.get("show_link_check"))
        toggle_row = (
            '<section class="panel" aria-label="Run filters" style="margin-bottom: 1rem;">'
            '<div class="panel-head">Run type filter</div>'
            '<div style="padding: 0.75rem 1rem;">'
            f'Crawl: <strong>{"on" if show_crawl else "off"}</strong> · '
            f'<a href="{esc(links.get("show_crawl_url", ""))}">toggle</a> · '
            f'Link-check: <strong>{"on" if show_link_check else "off"}</strong> · '
            f'<a href="{esc(links.get("show_link_check_url", ""))}">toggle</a>'
            "</div></section>"
        )
    return _render_results_shell(
        title=f"Blink results · {esc(job.get('job_id', 'job'))} · {esc(task_type)}",
        heading=f"Job results · {esc(job.get('job_id', ''))} · {esc(task_type)}",
        subtitle=f"{esc(job.get('name', ''))} ({esc('enabled' if job.get('enabled') else 'disabled')})",
        panel_title="Run history",
        table_headers=table_headers,
        table_rows=rows if rows else empty_row,
        footer_extra=(links.get("refresh", ""), "Refresh"),
        body_extra=tabs_row + toggle_row,
        branding_links=links,
    )


def render_results_structure_html(
    *,
    job: dict[str, Any],
    run: dict[str, Any],
    tree_payload: dict[str, Any],
    links: dict[str, str],
) -> str:
    """Render radial tidy tree structure view for one crawl run."""
    tree_json_raw = json.dumps(tree_payload, separators=(",", ":"), ensure_ascii=True)
    link_check_options: list[dict[str, Any]] = list(links.get("link_check_options") or [])
    selected_link_check_id = links.get("selected_link_check_id")
    selected_external_mode = str(links.get("selected_external_mode") or "none")
    selector_html = ""
    if link_check_options:
        options = ['<option value="">latest per target (all link-check runs)</option>']
        for option in link_check_options:
            run_id = int(option.get("run_id") or 0)
            selected = " selected" if selected_link_check_id is not None and int(selected_link_check_id) == run_id else ""
            started = esc(option.get("started_at") or "—")
            options.append(f'<option value="{run_id}"{selected}>run {run_id} · {started}</option>')
        selector_html = (
            '<label>Link-check run'
            f'<select id="linkCheckRun">{"".join(options)}</select>'
            "</label>"
        )
    external_selector_html = (
        '<label>External links'
        '<select id="externalMode">'
        f'<option value="none"{" selected" if selected_external_mode == "none" else ""}>none</option>'
        f'<option value="failed"{" selected" if selected_external_mode == "failed" else ""}>failed only</option>'
        f'<option value="ignored"{" selected" if selected_external_mode == "ignored" else ""}>ignored only</option>'
        "</select>"
        "</label>"
    )
    return _render_results_shell(
        title=f"Blink website structure · {esc(job.get('job_id', ''))} run {esc(run.get('run_id', ''))}",
        heading=f"Website structure · {esc(job.get('job_id', ''))}",
        subtitle=f"{esc(job.get('name', ''))} · run {esc(run.get('run_id', ''))}",
        panel_title="URL path radial tree",
        table_headers=["Field", "Value"],
        table_rows=(
            f"<tr><td>Metric</td><td class=\"mono\">{esc(tree_payload.get('metric') or 'external_count')}</td></tr>"
            f"<tr><td>Node count</td><td class=\"mono\">{esc(tree_payload.get('node_count') or 0)}</td></tr>"
            f"<tr><td>Leaf pages</td><td class=\"mono\">{esc(tree_payload.get('leaf_count') or 0)}</td></tr>"
            f"<tr><td>JSON</td><td>{_html_action_link(links.get('structure_json'), 'download')}</td></tr>"
            f"<tr><td>Run</td><td>{_html_action_link(links.get('run'), 'open run detail')}</td></tr>"
        ),
        footer_extra=(links.get("refresh", ""), "Refresh"),
        body_extra=f"""
<section class="panel" aria-label="Radial tree chart" style="margin-top: 1rem;">
  <div class="panel-head">Radial tidy tree</div>
  <div style="padding: 0.75rem 1rem; display: flex; gap: 1rem; flex-wrap: wrap; align-items: center;">
    <label>Color by
      <select id="colorBy">
        <option value="external_count" selected>external links</option>
        <option value="failed_count">failed links</option>
        <option value="ignored_count">ignored links</option>
      </select>
    </label>
    <label>Size by
      <select id="sizeBy">
        <option value="external_count" selected>external links</option>
        <option value="failed_count">failed links</option>
        <option value="ignored_count">ignored links</option>
        <option value="fixed">fixed</option>
      </select>
    </label>
    {selector_html}
    {external_selector_html}
  </div>
  <div id="radial-tree" style="width: 100%; height: 72vh; min-height: 420px; max-height: 960px; overflow: hidden; border-radius: 6px; border: 1px solid #e2e8f0; background: #f8fafc; touch-action: none;"></div>
  <div id="node-popover" class="panel" style="margin: 0.75rem 1rem 1rem 1rem; display: none;">
    <div class="panel-head">Node details</div>
    <div id="node-popover-content" style="padding: 0.75rem 1rem;"></div>
  </div>
</section>
<script id="structure-payload" type="application/json">{tree_json_raw}</script>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script>
(() => {{
  const payloadEl = document.getElementById("structure-payload");
  const payload = JSON.parse(payloadEl ? payloadEl.textContent : "{{}}");
  const treeExternalMode = payload.external_mode || "none";
  const pageContentApi = payload.page_content_api || null;
  const host = document.getElementById("radial-tree");
  const popover = document.getElementById("node-popover");
  const popoverContent = document.getElementById("node-popover-content");
  const colorBy = document.getElementById("colorBy");
  const sizeBy = document.getElementById("sizeBy");
  const linkCheckRun = document.getElementById("linkCheckRun");
  const externalMode = document.getElementById("externalMode");
  const width = 1000;
  const radius = width / 2;
  let zoomState = d3.zoomIdentity;
  let clickTimer = null;

  function flatten(node, out = []) {{
    out.push(node);
    (node.children || []).forEach((child) => flatten(child, out));
    return out;
  }}

  function externalUrlHostname(d) {{
    const raw = String(d.data.target_url || d.data.url || d.data.name || "").trim();
    try {{
      const host = new URL(raw).hostname;
      return host || raw;
    }} catch (_e) {{
      const m = /^https?:\\/\\/([^/?#]+)/i.exec(raw);
      return m ? m[1] : raw;
    }}
  }}

  function isFailedExternalUrlLabel(d) {{
    return (
      treeExternalMode === "failed"
      && d.data.node_kind === "external_url"
      && (Number(d.data.failed_count || 0) > 0 || d.data.ok === false)
    );
  }}

  function nodeDisplayText(d) {{
    if (d.data.node_kind === "external_domain") {{
      return `@${{d.data.name || ""}}`;
    }}
    if (isFailedExternalUrlLabel(d)) {{
      return externalUrlHostname(d);
    }}
    return d.data.name || "/";
  }}

  function nodeLabelFill(d) {{
    return isFailedExternalUrlLabel(d) ? "#dc2626" : "#0f172a";
  }}

  function escHtml(value) {{
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }}

  function buildPageContentFetchUrl(nodeUrl) {{
    if (!pageContentApi || !pageContentApi.href || !nodeUrl) return null;
    const u = new URL(pageContentApi.href, window.location.origin);
    u.searchParams.set("url", nodeUrl);
    if (pageContentApi.task_type) u.searchParams.set("task_type", String(pageContentApi.task_type));
    if (pageContentApi.link_check_run_id != null && pageContentApi.link_check_run_id !== "") {{
      u.searchParams.set("link_check_run_id", String(pageContentApi.link_check_run_id));
    }}
    return u.pathname + u.search;
  }}

  function pathRowHtml(url, fullPath) {{
    const raw = (url && String(url).trim()) || (fullPath && String(fullPath)) || "/";
    if (/^https?:\\/\\//i.test(raw)) {{
      return `<div><strong>URL:</strong> <a href="${{escHtml(raw)}}" target="_blank" rel="noopener noreferrer" class="mono">${{escHtml(raw)}}</a></div>`;
    }}
    return `<div><strong>Path:</strong> <span class="mono">${{escHtml(raw)}}</span></div>`;
  }}

  function canLoadPageContent(d) {{
    const kind = d.data.node_kind || "";
    if (kind !== "internal_page" && kind !== "internal_root") return false;
    const url = (d.data.url && String(d.data.url).trim()) || "";
    return Boolean(pageContentApi && pageContentApi.href && url && /^https?:\\/\\//i.test(url));
  }}

  function showNodeDetails(d) {{
    const url = d.data.url || "";
    const kind = d.data.node_kind || "internal";
    const detailsLink = d.data.details_url ? `<a href="${{escHtml(d.data.details_url)}}">Open run details</a>` : "";
    const collapsedState = d.data._children && d.data._children.length > 0 ? "collapsed" : "expanded";
    const showContent = canLoadPageContent(d);
    const contentSection = showContent
      ? `<div style="margin-top: 0.6rem;">
          <button type="button" id="structureShowContentBtn">Show content</button>
          <div id="structureContentStatus" style="margin-top: 0.35rem; font-size: 12px; color: #64748b;"></div>
          <pre id="structurePageContent" style="display:none; margin-top: 0.5rem; padding: 0.6rem; max-height: 40vh; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a0a; border-radius: 6px; font-size: 12px;"></pre>
        </div>`
      : "";
    popoverContent.innerHTML = `
      ${{pathRowHtml(url, d.data.full_path)}}
      <div><strong>Node kind:</strong> <span class="mono">${{escHtml(kind)}}</span></div>
      <div><strong>External links:</strong> <span class="mono">${{escHtml(d.data.external_count || 0)}}</span></div>
      <div><strong>Failed links:</strong> <span class="mono">${{escHtml(d.data.failed_count || 0)}}</span></div>
      <div><strong>Ignored links:</strong> <span class="mono">${{escHtml(d.data.ignored_count || 0)}}</span></div>
      <div><strong>Node state:</strong> <span class="mono">${{escHtml(collapsedState)}}</span></div>
      <div><strong>Status:</strong> <span class="mono">${{escHtml(d.data.ok === false ? "failed" : "ok")}}</span></div>
      <div style="margin-top: 0.4rem;">${{detailsLink}}</div>
      ${{contentSection}}
    `;
    popover.style.display = "block";
    const btn = document.getElementById("structureShowContentBtn");
    const out = document.getElementById("structurePageContent");
    const st = document.getElementById("structureContentStatus");
    if (btn && out && st) {{
      btn.addEventListener("click", async () => {{
        const fetchPath = buildPageContentFetchUrl(String(url).trim());
        if (!fetchPath) return;
        st.textContent = "Loading…";
        btn.disabled = true;
        try {{
          const res = await fetch(fetchPath, {{ credentials: "same-origin" }});
          const body = await res.json().catch(() => ({{}}));
          if (!res.ok) {{
            st.textContent = res.status === 400 ? "Invalid request." : "Could not load content.";
            return;
          }}
          if (body.main_text == null || body.main_text === "") {{
            st.textContent = "No stored text for this page in this run (enable main text extraction in the job).";
            out.style.display = "none";
            return;
          }}
          st.textContent = body.truncated ? "Showing first portion of stored text (truncated)." : "";
          out.textContent = String(body.main_text);
          out.style.display = "block";
        }} catch (e) {{
          st.textContent = "Network error while loading content.";
        }} finally {{
          btn.disabled = false;
        }}
      }}, false);
    }}
  }}

  function toggleCollapse(d) {{
    if (d.data.children && d.data.children.length > 0) {{
      d.data._children = d.data.children;
      d.data.children = [];
    }} else if (d.data._children && d.data._children.length > 0) {{
      d.data.children = d.data._children;
      d.data._children = [];
    }}
  }}

  function nodeHasToggle(d) {{
    const vis = d.data.children && d.data.children.length > 0;
    const hid = d.data._children && d.data._children.length > 0;
    return vis || hid;
  }}

  function nodeIsCollapsed(d) {{
    return Boolean(d.data._children && d.data._children.length > 0 && !(d.data.children && d.data.children.length > 0));
  }}

  function render() {{
    host.innerHTML = "";
    const root = d3.hierarchy(payload.nodes);
    d3.tree().size([2 * Math.PI, radius - 120])(root);
    const allNodes = flatten(payload.nodes);
    const key = colorBy.value || "external_count";
    const keyForSize = sizeBy.value || "external_count";
    const maxMetric = Math.max(1, ...allNodes.map((n) => n[key] || 0));
    const maxSizeMetric = Math.max(1, ...allNodes.map((n) => n[keyForSize] || 0));
    const colorInterpolator = (key === "failed_count" || key === "ignored_count")
      ? d3.interpolateRgb("#ffffff", "#dc2626")
      : d3.interpolateBlues;
    const colorScale = d3.scaleSequential([0, maxMetric], colorInterpolator);
    const sizeRMin = 2;
    const sizeRMax = 2 + 3 * (8 - 2.5);
    const sizeScale = d3.scaleSqrt().domain([0, maxSizeMetric]).range([sizeRMin, sizeRMax]);

    const svg = d3.create("svg")
      .attr("viewBox", [-radius, -radius, width, width])
      .attr("width", "100%")
      .attr("height", "100%")
      .style("font", "11px sans-serif")
      .style("display", "block");

    const chartLayer = svg.append("g").attr("class", "chart-layer");

    chartLayer.append("g")
      .attr("fill", "none")
      .attr("stroke", "#cbd5e1")
      .attr("stroke-opacity", 0.8)
      .selectAll("path")
      .data(root.links())
      .join("path")
      .attr("d", d3.linkRadial().angle((d) => d.x).radius((d) => d.y));

    const node = chartLayer.append("g")
      .selectAll("g")
      .data(root.descendants())
      .join("g")
      .attr("transform", (d) => `rotate(${{(d.x * 180 / Math.PI) - 90}}) translate(${{d.y}},0)`)
      .style("cursor", "pointer");

    node.each(function(d) {{
      const g = d3.select(this);
      const r = sizeBy.value === "fixed" ? 4 : sizeScale(d.data[keyForSize] || 0);
      const collapsed = nodeIsCollapsed(d);
      const toggleable = nodeHasToggle(d);
      g.selectAll(".node-shape").remove();
      const shape = g.append("g").attr("class", "node-shape");
      if (toggleable && collapsed) {{
        shape.append("text")
          .attr("text-anchor", "middle")
          .attr("dy", "0.35em")
          .attr("fill", "#0f172a")
          .style("font-size", `${{Math.max(11, r * 2.4)}}px`)
          .style("font-weight", "700")
          .text("+");
      }} else {{
        shape.append("circle")
          .attr("r", r)
          .attr("fill", () => colorScale(d.data[key] || 0))
          .attr("stroke", "#1e293b")
          .attr("stroke-width", 0.6);
      }}
    }});

    node.append("text")
      .attr("dy", "0.31em")
      .attr("x", (d) => d.x < Math.PI === !d.children ? 8 : -8)
      .attr("text-anchor", (d) => d.x < Math.PI === !d.children ? "start" : "end")
      .attr("transform", (d) => d.x >= Math.PI ? "rotate(180)" : null)
      .attr("fill", (d) => nodeLabelFill(d))
      .text((d) => nodeDisplayText(d))
      .clone(true).lower()
      .attr("stroke", "white");

    const zoom = d3.zoom()
      .scaleExtent([0.12, 14])
      .filter((event) => (!event.ctrlKey || event.type === "wheel") && !event.button && event.type !== "dblclick")
      .on("zoom", (event) => {{
        zoomState = event.transform;
        chartLayer.attr("transform", event.transform);
      }});

    svg.call(zoom).on("dblclick.zoom", null);
    svg.call(zoom.transform, zoomState);

    node.on("click", (event, d) => {{
      event.stopPropagation();
      if (clickTimer) window.clearTimeout(clickTimer);
      clickTimer = window.setTimeout(() => {{
        clickTimer = null;
        showNodeDetails(d);
      }}, 280);
    }});

    node.on("dblclick", (event, d) => {{
      event.preventDefault();
      event.stopPropagation();
      if (clickTimer) {{
        window.clearTimeout(clickTimer);
        clickTimer = null;
      }}
      if (!nodeHasToggle(d)) return;
      toggleCollapse(d);
      render();
    }});

    host.append(svg.node());
  }}

  colorBy.addEventListener("change", render);
  sizeBy.addEventListener("change", render);
  if (linkCheckRun) {{
    linkCheckRun.addEventListener("change", () => {{
      const selected = linkCheckRun.value;
      const url = new URL(window.location.href);
      if (selected) {{
        url.searchParams.set("link_check_run_id", selected);
      }} else {{
        url.searchParams.delete("link_check_run_id");
      }}
      window.location.href = url.toString();
    }});
  }}
  if (externalMode) {{
    externalMode.addEventListener("change", () => {{
      const selected = externalMode.value || "none";
      const url = new URL(window.location.href);
      url.searchParams.set("external_mode", selected);
      window.location.href = url.toString();
    }});
  }}
  render();
}})();
</script>
""",
        branding_links=links,
    )


def _history_run_row(run: dict[str, Any], *, child: bool) -> str:
    def _fmt_count(value: object) -> str:
        return esc(value if value not in (None, "") else "—")

    run_id = run.get("run_id")
    run_id_cell = (
        f'<span class="run-child-marker" aria-hidden="true">{_LINK_CHECK_CHILD_MARKER}</span> '
        f'<span class="mono">{esc(run_id)}</span>'
        if child
        else f'<span class="mono">{esc(run_id)}</span>'
    )
    row_class = ' class="row-link-check"' if child else ""
    started = esc(_fmt_dt_short(run.get("started_at")))
    finished = esc(_fmt_dt_short(run.get("finished_at"), ongoing=True))
    if child:
        visited = _fmt_count(run.get("checked_total"))
        failed = _fmt_count(run.get("failed_total"))
    else:
        visited = _fmt_count(run.get("pages_visited"))
        failed = _fmt_count(run.get("pages_failed"))
    report_cell = _html_action_link(run.get("details_url"), "report")
    logs_cell = _html_log_links_cell(run)
    json_cell = _html_report_link_cell(run)
    return f"""<tr{row_class}>
    <td>{run_id_cell}</td>
    <td class="mono time">{started}</td>
    <td class="mono time">{finished}</td>
    <td class="mono">{visited}</td>
    <td class="mono">{failed}</td>
    <td>{report_cell}</td>
    <td>{logs_cell}</td>
    <td>{json_cell}</td>
  </tr>"""


def render_file_viewer_html(
    *,
    title: str,
    heading: str,
    subtitle: str,
    panel_title: str,
    body_html: str,
    download_url: str,
    download_label: str,
    links: dict[str, str],
) -> str:
    """Dashboard page for inline log or JSON report content."""
    download_btn = (
        f'<a class="viewer-download" href="{esc(download_url)}">{esc(download_label)}</a>'
        if download_url.strip()
        else ""
    )
    body_extra = f"""
<section class="panel viewer-panel" aria-label="{esc(panel_title)}">
  <div class="panel-head viewer-head">{esc(panel_title)}{download_btn}</div>
  {body_html}
</section>
"""
    return _render_results_shell(
        title=title,
        heading=heading,
        subtitle=subtitle,
        panel_title="",
        table_headers=[],
        table_rows="",
        footer_extra=(links.get("back", links.get("refresh", "")), "Back to history"),
        body_extra=body_extra,
        show_table_header=False,
        branding_links=links,
    )


def render_job_task_history_html(
    *,
    job: dict[str, Any],
    crawl_runs: list[dict[str, Any]],
    links: dict[str, str],
) -> str:
    """Render crawl history with related link-check runs nested under each crawl."""

    rows: list[str] = []
    for crawl in crawl_runs:
        rows.append(_history_run_row(crawl, child=False))
        for link_run in list(crawl.get("link_checks") or []):
            rows.append(_history_run_row(link_run, child=True))

    return _render_results_shell(
        title=f"Blink history · {esc(job.get('job_id', ''))}",
        heading=f"Task history · {esc(job.get('job_id', ''))}",
        subtitle=f"{esc(job.get('name', ''))} ({esc('enabled' if job.get('enabled') else 'disabled')})",
        panel_title="Crawl runs with related link-check runs",
        table_headers=["Run", "Started", "Finished", "Visited/Checked", "Failed", "Report", "Logs", "JSON"],
        table_rows="".join(rows) if rows else '<tr><td colspan="8" class="empty">No run history available.</td></tr>',
        footer_extra=(links.get("refresh", ""), "Refresh"),
        branding_links=links,
    )


def render_results_run_html(
    *,
    job: dict[str, Any],
    run: dict[str, Any],
    totals: dict[str, int],
    failed_summary: dict[str, Any],
    filters: dict[str, Any],
    failed_links: list[dict[str, Any]],
    failed_pages: list[dict[str, Any]],
    ignored_links: list[dict[str, Any]],
    links: dict[str, str],
) -> str:
    """Render one run's crawl/link-check details."""

    comparison_run_ids: list[int] = list(failed_summary.get("comparison_run_ids") or [])
    per_run_counts: dict[int, dict[str, int]] = dict(failed_summary.get("per_run_category_counts") or {})
    all_categories: set[str] = set()
    for counts in per_run_counts.values():
        all_categories.update(counts.keys())

    based_on_metric = ""
    if str(run.get("task_type") or "crawl") == "link_check":
        based_on_metric = (
            f'<div class="metric"><div class="value">{esc(run.get("link_check_run_id") or run.get("run_id"))}</div>'
            '<div class="label">Link-check run id</div></div>'
            f'<div class="metric"><div class="value">{esc(run.get("based_on_crawl_run_id") or "—")}</div>'
            '<div class="label">Based on crawl run id</div></div>'
        )
    run_stats = f"""
<section class="metrics" aria-label="Run summary">
  <div class="metric"><div class="value">{esc(run.get("pages_visited"))}</div><div class="label">Pages visited <span class="info-icon" title="Number of pages crawled in this run.">(i)</span></div></div>
  <div class="metric"><div class="value">{esc(totals.get("pages_total"))}</div><div class="label">Pages covered <span class="info-icon" title="Distinct internal pages currently known for this job DB.">(i)</span></div></div>
  <div class="metric"><div class="value">{esc(_fmt_count_with_ratio(totals.get("external_links_total"), totals.get("pages_total")))}</div><div class="label">External links <span class="info-icon" title="Distinct external target URLs currently known for this job DB. Ratio is vs pages covered.">(i)</span></div></div>
  <div class="metric"><div class="value">{esc(_fmt_count_with_ratio(failed_summary.get("failed_total"), totals.get("external_links_total")))}</div><div class="label">Failed links <span class="info-icon" title="Latest failed link-check targets in this run before filtering. Ratio is vs external links.">(i)</span></div></div>
  <div class="metric"><div class="value">{esc(_fmt_count_with_ratio(failed_summary.get("ignored_total", 0), totals.get("external_links_total")))}</div><div class="label">Ignored links <span class="info-icon" title="Latest link-check targets suppressed by ignore rules (status/category/message/domain). Ratio is vs external links.">(i)</span></div></div>
  {based_on_metric}
</section>
"""
    column_labels = [f"run {rid}" for rid in comparison_run_ids]
    category_rows_list: list[str] = []
    for category in sorted(all_categories):
        counts = [int(per_run_counts.get(rid, {}).get(category, 0)) for rid in comparison_run_ids]
        cells: list[str] = []
        for i, count in enumerate(counts):
            if i + 1 < len(counts):
                diff = count - counts[i + 1]
                diff_str = f"{diff:+d}"
                cells.append(f"{count} ({diff_str})")
            else:
                cells.append(f"{count} (n/a)")
        cells_html = "".join(f'<td class="mono">{esc(v)}</td>' for v in cells)
        category_rows_list.append(f"<tr><td>{esc(category)}</td>{cells_html}</tr>")
    category_rows = "".join(category_rows_list)
    if not category_rows:
        category_rows = '<tr><td colspan="4" class="empty">No failed external links in this run.</td></tr>'

    status_options = sorted(list(filters.get("status_options") or []))
    category_options = sorted(list(filters.get("category_options") or []))
    include_status_selected = set(filters.get("include_status") or [])
    include_category_selected = set(filters.get("include_category") or [])
    filter_action = esc(filters.get("filter_action") or "")
    clear_filters_url = esc(filters.get("clear_filters_url") or "")

    include_status_option_html = []
    include_status_option_html.extend(
        f"<option value=\"{esc(v)}\"{' selected' if v in include_status_selected else ''}>{esc(v)}</option>"
        for v in status_options
    )
    include_category_option_html = []
    include_category_option_html.extend(
        f"<option value=\"{esc(v)}\"{' selected' if v in include_category_selected else ''}>{esc(v)}</option>"
        for v in category_options
    )

    failed_link_rows = "\n".join(
        f"""<tr>
    <td class="source-col">{_render_target_and_sources(item)}</td>
    <td class="mono">{esc(item.get("status_code") if item.get("status_code") is not None else "—")}</td>
    <td>{esc(item.get("error_category") or "—")}</td>
    <td class="wrap-col">{esc(item.get("error_message") or "—")}</td>
    <td class="mono">{esc(item.get("checked_at") or "—")}</td>
  </tr>"""
        for item in failed_links
    )
    if not failed_link_rows:
        failed_link_rows = '<tr><td colspan="5" class="empty">No failed links for this run.</td></tr>'

    failed_page_rows = "\n".join(
        f"""<tr>
    <td class="mono">{esc(item.get("url", ""))}</td>
    <td class="mono">{esc(item.get("depth", ""))}</td>
    <td class="mono">{esc(item.get("status_code") if item.get("status_code") is not None else "—")}</td>
    <td>{esc(item.get("error_message") or "—")}</td>
    <td class="mono">{esc(item.get("created_at") or "—")}</td>
  </tr>"""
        for item in failed_pages
    )
    if not failed_page_rows:
        failed_page_rows = '<tr><td colspan="5" class="empty">No failed crawl pages for this run.</td></tr>'

    ignored_link_rows = "\n".join(
        f"""<tr>
    <td class="source-col">{_render_target_and_sources(item)}</td>
    <td class="mono">{esc(item.get("status_code") if item.get("status_code") is not None else "—")}</td>
    <td>{esc(item.get("error_category") or "—")}</td>
    <td class="wrap-col">{esc(item.get("decision_reason") or "—")}</td>
    <td class="wrap-col">{esc(item.get("error_message") or "—")}</td>
    <td class="mono">{esc(item.get("checked_at") or "—")}</td>
  </tr>"""
        for item in ignored_links
    )
    if not ignored_link_rows:
        ignored_link_rows = '<tr><td colspan="6" class="empty">No ignored link-check results for this run.</td></tr>'

    body_extra = f"""
{run_stats}
<section class="panel" aria-label="Failed by category">
  <div class="panel-head">Failed external links by error category</div>
  <div class="panel-scroll">
  <table class="data-table">
    <thead><tr><th>Error category</th>{''.join(f'<th>{esc(label)}</th>' for label in column_labels)}</tr></thead>
    <tbody>{category_rows}</tbody>
  </table>
  </div>
</section>
<section class="panel" aria-label="Global filters">
  <div class="panel-head">Global filters</div>
  <div class="filters-row">
    <form method="get" action="{filter_action}" class="filters-form">
      <label>Include status
        <select name="include_status" multiple size="4">{''.join(include_status_option_html)}</select>
      </label>
      <label>Include category
        <select name="include_category" multiple size="4">{''.join(include_category_option_html)}</select>
      </label>
      <button type="submit">Apply filters</button>
      <a href="{clear_filters_url}">Clear</a>
    </form>
  </div>
</section>
<section class="panel" aria-label="Failed links" style="margin-top: 1rem;">
  <div class="panel-head">Failed link-check results (latest per target)</div>
  <div class="panel-scroll">
  <table class="data-table sticky-head">
    <thead>
      <tr><th>Target URL</th><th>Status</th><th>Category</th><th>Error</th><th>Checked at</th></tr>
    </thead>
    <tbody>{failed_link_rows}</tbody>
  </table>
  </div>
</section>
<section class="panel" aria-label="Failed crawl pages" style="margin-top: 1rem;">
  <div class="panel-head">Failed crawl pages</div>
  <div class="panel-scroll">
  <table class="data-table">
    <thead>
      <tr><th>URL</th><th>Depth</th><th>Status</th><th>Error</th><th>Created</th></tr>
    </thead>
    <tbody>{failed_page_rows}</tbody>
  </table>
  </div>
</section>
<section class="panel" aria-label="Ignored external links" style="margin-top: 1rem;">
  <div class="panel-head">Ignored link-check results (latest per target)</div>
  <div class="panel-scroll">
  <table class="data-table">
    <thead><tr><th>Target URL</th><th>Status</th><th>Category</th><th>Reason</th><th>Error</th><th>Checked at</th></tr></thead>
    <tbody>{ignored_link_rows}</tbody>
  </table>
  </div>
</section>
"""
    structure_cell = _html_action_link(links.get("structure"), "website structure")
    run_json_cell = _html_action_link(links.get("run_json"), "json")
    return _render_results_shell(
        title=f"Blink results · {esc(job.get('job_id', ''))} run {esc(run.get('run_id', ''))}",
        heading=f"Run detail · {esc(job.get('job_id', ''))}",
        subtitle=f"{esc(job.get('name', ''))} ({esc('enabled' if job.get('enabled') else 'disabled')})",
        panel_title="Overview",
        table_headers=["Field", "Value"],
        table_rows=(
            f"<tr><td>job id</td><td class=\"mono\">{esc(job.get('job_id', ''))}</td></tr>"
            f"<tr><td>job name</td><td>{esc(job.get('name', ''))}</td></tr>"
            f"<tr><td>run start</td><td class=\"mono\">{esc(run.get('started_at') or '—')}</td></tr>"
            f"<tr><td>run end</td><td class=\"mono\">{esc(run.get('finished_at') or '—')}</td></tr>"
            f"<tr><td>run logs</td><td>{_html_log_links_cell(run)}</td></tr>"
            f"<tr><td>website structure</td><td>{structure_cell}</td></tr>"
            f"<tr><td>run data</td><td>{run_json_cell}</td></tr>"
        ),
        footer_extra=(links.get("refresh", ""), "Refresh"),
        body_extra=body_extra,
        show_table_header=False,
        branding_links=links,
    )


def _render_results_shell(
    *,
    title: str,
    heading: str,
    subtitle: str,
    panel_title: str,
    table_headers: list[str],
    table_rows: str,
    footer_extra: tuple[str, str] | None = None,
    body_extra: str = "",
    show_table_header: bool = True,
    branding_links: dict[str, str] | None = None,
) -> str:
    branding_links = branding_links or {}
    nav_html = _render_header_nav(branding_links)
    headers_html = "".join(f"<th>{esc(h)}</th>" for h in table_headers)
    gen_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    footer_html = _footer_links_html(branding_links, gen_at=gen_at, extra=footer_extra)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{esc(title)}</title>
  {_favicon_head(branding_links)}
  {_shared_styles()}
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      {_brand_header(branding_links)}
      <h1>{esc(heading)}</h1>
      <p>{esc(subtitle)}</p>
      <nav class="nav" aria-label="Navigation">
        {nav_html}
      </nav>
    </div>
  </header>
  <div class="wrap">
    <section class="panel" aria-label="Main table">
      <div class="panel-head">{esc(panel_title)}</div>
      <table>
        {'<thead><tr>' + headers_html + '</tr></thead>' if show_table_header else ''}
        <tbody>{table_rows}</tbody>
      </table>
    </section>
    {body_extra}
    <footer>{footer_html}</footer>
  </div>
</body>
</html>
"""


def _shared_styles() -> str:
    return """
<style>
  :root {
    --cardano-blue: #121f63;
    --cardano-blue-light: #1e3a8a;
    --surface: #f4f6fb;
    --card: #ffffff;
    --text: #1e293b;
    --muted: #64748b;
    --border: #e2e8f0;
    --success: #059669;
    --danger: #dc2626;
    --warn: #d97706;
    --radius: 12px;
    --shadow: 0 4px 24px rgba(18, 31, 99, 0.08);
    --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: var(--font); background: var(--surface); color: var(--text); line-height: 1.5; min-height: 100vh; }
  .hero { background: linear-gradient(135deg, var(--cardano-blue) 0%, var(--cardano-blue-light) 100%); color: #fff; padding: 2rem 1.5rem 2.25rem; box-shadow: var(--shadow); }
  .hero-inner { max-width: 1200px; margin: 0 auto; }
  .brand { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.35rem; }
  .brand-logo { width: 36px; height: 36px; object-fit: contain; border-radius: 8px; background: rgba(255, 255, 255, 0.14); padding: 4px; }
  .brand-name { font-size: 2.25rem; line-height: 36px; font-weight: 700; letter-spacing: -0.02em; }
  .brand-tagline { margin: 0 0 0.75rem; font-size: 1.125rem; font-weight: 500; opacity: 0.92; letter-spacing: 0.01em; }
  .job-title { font-weight: 700; margin-bottom: 0.25rem; }
  .job-line { margin-top: 0.2rem; }
  .hero h1 { margin: 0 0 0.35rem; font-size: 1.65rem; font-weight: 700; letter-spacing: -0.02em; }
  .hero p { margin: 0; opacity: 0.92; font-size: 0.95rem; max-width: 42rem; }
  .nav { margin-top: 1rem; display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; font-size: 0.875rem; }
  .nav a { color: #fff; opacity: 0.95; text-decoration: underline; text-underline-offset: 3px; }
  .nav a:hover { opacity: 1; }
  .auth-user { opacity: 0.9; margin-left: 0.5rem; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 1.5rem; }
  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; margin-bottom: 1rem; }
  .metric { background: var(--card); border-radius: var(--radius); padding: 1.15rem 1.25rem; box-shadow: var(--shadow); border: 1px solid var(--border); }
  .metric .value { font-size: 1.75rem; font-weight: 700; color: var(--cardano-blue); letter-spacing: -0.03em; }
  .metric .label { font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; text-transform: uppercase; letter-spacing: 0.04em; }
  .info-icon { font-size: 0.72rem; cursor: help; text-transform: none; }
  .panel { background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow); border: 1px solid var(--border); overflow: hidden; }
  .panel-head { padding: 1rem 1.25rem; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 1rem; color: var(--cardano-blue); }
  table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
  th { text-align: left; padding: 0.65rem 1rem; background: #f8fafc; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; font-size: 0.7rem; border-bottom: 1px solid var(--border); }
  td { padding: 0.65rem 1rem; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:nth-child(even) td { background: #fafbfd; }
  tr:last-child td { border-bottom: none; }
  td.empty { text-align: center; color: var(--muted); padding: 2rem; }
  .panel-scroll {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-x: contain;
    max-width: 100%;
  }
  .panel-scroll .data-table {
    width: max-content;
    min-width: 100%;
  }
  .panel-scroll .data-table th,
  .panel-scroll .data-table td {
    white-space: nowrap;
  }
  .panel-scroll .data-table .source-col {
    white-space: normal;
    min-width: 14rem;
    max-width: 24rem;
  }
  .panel-scroll .data-table .wrap-col {
    white-space: normal;
    min-width: 10rem;
    max-width: 22rem;
    word-break: break-word;
  }
  .source-col { min-width: 14rem; }
  .filters-row { padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); background: #fff; }
  .filters-form { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
  .filters-form label { font-size: 0.8rem; color: var(--muted); }
  .filters-form select { margin-left: 0.35rem; min-width: 10rem; border-radius: 8px; border: 1px solid var(--border); padding: 0.2rem 0.25rem; background: #fff; }
  .target-url { font-weight: 700; margin-bottom: 0.35rem; }
  .source-list { margin-top: 0.2rem; }
  .source-row { display: block; margin-top: 0.15rem; }
  .source-arrow { margin-right: 0.2rem; color: var(--muted); }
  .source-link { display: inline; }
  .sticky-head thead th { position: sticky; top: 0; z-index: 1; }
  .mono { font-family: ui-monospace, "Cascadia Code", "SF Mono", Menlo, monospace; font-size: 0.78rem; }
  .time { white-space: nowrap; }
  .run-child-marker { color: var(--muted); margin-right: 0.15rem; }
  tr.row-link-check td:first-child { padding-left: 2rem; }
  .viewer-panel { margin-top: 0; }
  .viewer-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
  .viewer-download { font-size: 0.8rem; font-weight: 600; text-decoration: none; color: var(--cardano-blue); border: 1px solid var(--border); border-radius: 8px; padding: 0.35rem 0.75rem; background: #fff; }
  .viewer-download:hover { background: #f8fafc; }
  .viewer-downloads { display: flex; flex-wrap: wrap; gap: 0.5rem; padding: 0.75rem 1.25rem 0; }
  .viewer-body { padding: 1rem 1.25rem 1.25rem; }
  .viewer-day-label { font-size: 0.75rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin: 1rem 0 0.35rem; }
  .viewer-day-label:first-child { margin-top: 0; }
  .viewer-pre { margin: 0; padding: 0.85rem 1rem; background: #0f172a; color: #e2e8f0; border-radius: 8px; overflow: auto; max-height: 70vh; font-size: 0.75rem; line-height: 1.45; white-space: pre-wrap; word-break: break-word; }
  footer { margin-top: 2rem; padding: 1rem 0; font-size: 0.75rem; color: var(--muted); text-align: center; }
</style>
"""


def esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _favicon_head(links: dict[str, str]) -> str:
    bits: list[str] = []
    if links.get("favicon_ico"):
        bits.append(f'<link rel="icon" href="{esc(links["favicon_ico"])}" sizes="any"/>')
    if links.get("favicon_svg"):
        bits.append(f'<link rel="icon" href="{esc(links["favicon_svg"])}" type="image/svg+xml"/>')
    if links.get("apple_touch_icon"):
        bits.append(f'<link rel="apple-touch-icon" href="{esc(links["apple_touch_icon"])}"/>')
    if links.get("manifest"):
        bits.append(f'<link rel="manifest" href="{esc(links["manifest"])}"/>')
    return "\n  ".join(bits)


def _brand_header(links: dict[str, str]) -> str:
    logo_url = str(links.get("logo_url") or "").strip()
    if not logo_url:
        return ""
    return (
        '<div class="brand" aria-label="Branding">'
        f'<img src="{esc(logo_url)}" alt="Blink logo" class="brand-logo"/>'
        '<span class="brand-name">Blink</span>'
        "</div>"
    )


def _auth_form_styles() -> str:
    return """
<style>
  .auth-form { padding: 1.25rem 1.5rem 1.5rem; max-width: 32rem; }
  .auth-form label { display: block; margin: 0.85rem 0 0.35rem; font-size: 0.8rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; }
  .auth-form label:first-of-type { margin-top: 0; }
  .auth-form input[type="email"],
  .auth-form input[type="password"] { width: 100%; padding: 0.55rem 0.65rem; border: 1px solid var(--border); border-radius: 8px; font-size: 0.95rem; font-family: inherit; }
  .auth-form input:focus { outline: 2px solid rgba(18, 31, 99, 0.25); border-color: var(--cardano-blue); }
  .auth-form button[type="submit"] { margin-top: 1.35rem; padding: 0.6rem 1.35rem; background: var(--cardano-blue); color: #fff; border: none; border-radius: 8px; font-weight: 600; font-size: 0.9rem; cursor: pointer; }
  .auth-form button[type="submit"]:hover { background: var(--cardano-blue-light); }
  .auth-notice { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: var(--radius); padding: 0.9rem 1rem; margin-bottom: 0.25rem; font-size: 0.875rem; color: var(--text); line-height: 1.45; }
  .auth-notice strong { color: var(--cardano-blue); }
  .auth-err { color: var(--danger); margin-bottom: 0.75rem; font-size: 0.875rem; font-weight: 600; }
  .auth-plain-error { padding: 1.25rem 1.5rem; font-size: 0.9rem; line-height: 1.55; white-space: pre-wrap; color: var(--text); }
</style>
"""


def render_auth_page(
    *,
    title: str,
    heading: str,
    subtitle: str,
    panel_title: str,
    panel_body_html: str,
    branding_links: dict[str, str],
) -> str:
    """Dashboard-styled shell for login / set-password pages."""
    nav_html = _render_header_nav(branding_links)
    gen_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    footer_html = _footer_links_html(branding_links, gen_at=gen_at)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{esc(title)}</title>
  {_favicon_head(branding_links)}
  {_shared_styles()}
  {_auth_form_styles()}
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      {_brand_header(branding_links)}
      <h1>{esc(heading)}</h1>
      <p>{esc(subtitle)}</p>
      {f'<nav class="nav" aria-label="Navigation">{nav_html}</nav>' if nav_html else ''}
    </div>
  </header>
  <div class="wrap">
    <section class="panel" aria-label="{esc(panel_title)}">
      <div class="panel-head">{esc(panel_title)}</div>
      {panel_body_html}
    </section>
    <footer>{footer_html}</footer>
  </div>
</body>
</html>
"""


def _render_link_list(urls: list[str]) -> str:
    if not urls:
        return "—"
    return "<br/>".join(
        f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(url)}</a>'
        for url in urls
    )


def _render_target_and_sources(item: dict[str, Any]) -> str:
    target_url = str(item.get("target_url", "") or "")
    target_href = str(item.get("target_href", target_url) or target_url)
    source_urls = list(item.get("source_page_hrefs") or item.get("source_pages") or [])
    source_rows = "".join(
        f'<span class="source-row"><span class="source-arrow" aria-hidden="true">&#8632;</span>'
        f'<a class="source-link mono" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(_source_label(url))}</a></span>'
        for url in source_urls
    )
    if not source_rows:
        source_rows = '<span class="mono">—</span>'
    return (
        f'<div class="target-url"><a href="{esc(target_href)}" target="_blank" rel="noopener noreferrer">{esc(target_url)}</a></div>'
        f'<div class="source-list">{source_rows}</div>'
    )


def _source_label(url: str) -> str:
    """Return compact label (path/query/fragment) for source URLs."""
    raw = str(url or "").strip()
    if not raw:
        return "—"
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        path = (parsed.path or "").lstrip("/")
        query = f"?{parsed.query}" if parsed.query else ""
        fragment = f"#{parsed.fragment}" if parsed.fragment else ""
        compact = f"{path}{query}{fragment}"
        return compact or "/"
    return raw.lstrip("/") or raw


def _fmt_count_with_ratio(value: object, total: object) -> str:
    try:
        count = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return "—"
    try:
        base = int(total) if total is not None else 0
    except (TypeError, ValueError):
        base = 0
    ratio = int(round((count / base) * 100.0)) if base > 0 else 0
    return f"{count} ({ratio}%)"
