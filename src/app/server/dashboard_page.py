"""HTML for scheduler/results dashboards (proxy-safe and route-aware)."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit


def _html_log_links_cell(run: dict[str, Any]) -> str:
    pairs = list(run.get("log_links") or [])
    if not pairs:
        return "—"
    return " ".join(
        f'<a href="{html.escape(str(url))}">{html.escape(str(d))}</a>' for url, d in pairs
    )


def _html_report_link_cell(run: dict[str, Any]) -> str:
    url = str(run.get("report_url") or "").strip()
    if not url:
        return "—"
    return f'<a href="{html.escape(url)}">json</a>'


def render_schedule_dashboard_html(payload: dict[str, Any], *, links: dict[str, str]) -> str:
    """Build scheduler dashboard HTML using request-aware links."""
    tasks: list[dict[str, Any]] = list(payload.get("tasks") or [])
    crawl_n = len(payload.get("crawl_tasks") or [])
    link_n = len(payload.get("link_check_tasks") or [])
    total = len(tasks)
    scheduler_on = bool(payload.get("scheduler_running"))
    jobs_root = html.escape(str(payload.get("jobs_root") or ""))

    def esc(s: object) -> str:
        return html.escape("" if s is None else str(s))

    grouped: dict[str, dict[str, Any]] = {}
    for t in tasks:
        job_id = str(t.get("job_id") or "")
        if not job_id:
            continue
        entry = grouped.setdefault(job_id, {"job_id": job_id, "crawl": None, "link_check": None})
        task_type = str(t.get("task_type") or "")
        if task_type in {"crawl", "link_check"}:
            entry[task_type] = t

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

    def _line_last(label: str, task: dict[str, Any] | None) -> str:
        rt = (task or {}).get("runtime") or {}
        details_url = str((task or {}).get("latest_crawl_url" if label == "last crawl" else "latest_link_url") or "")
        when = _fmt_dt_short(rt.get("last_end_at"))
        when_cell = f'<a href="{esc(details_url)}" class="mono">{esc(when)}</a>' if details_url and when != "—" else f'<span class="mono">{esc(when)}</span>'
        if label == "last crawl":
            latest_crawl = (task or {}).get("latest_crawl") or {}
            metrics = (
                f'pages: {esc(latest_crawl.get("pages_visited") if latest_crawl.get("pages_visited") is not None else "—")} · '
                f'ignored: {esc((task or {}).get("ignored_total") if (task or {}).get("ignored_total") is not None else "—")} · '
                f'ext.links: {esc((task or {}).get("external_total") if (task or {}).get("external_total") is not None else "—")}'
            )
        else:
            latest_link = (task or {}).get("latest_link") or {}
            metrics = (
                f'tested: {esc(latest_link.get("checked_total") if latest_link.get("checked_total") is not None else "—")} · '
                f'ignored: {esc((task or {}).get("ignored_total") if (task or {}).get("ignored_total") is not None else "—")} '
                f'({esc((task or {}).get("ignored_ratio") if (task or {}).get("ignored_ratio") is not None else 0)}%) · '
                f'failed: {esc((task or {}).get("failed_total") if (task or {}).get("failed_total") is not None else "—")} '
                f'({esc((task or {}).get("failed_ratio") if (task or {}).get("failed_ratio") is not None else 0)}%)'
            )
        return (
            f'<div class="job-line-grid"><span class="line-label">{esc(label)}:</span>'
            f'<span class="line-date">{when_cell}</span>'
            f'<span class="line-mid mono">{metrics}</span>'
            f'<span class="line-status">{_status_cell(rt)}</span></div>'
        )

    def _line_next(label: str, task: dict[str, Any] | None) -> str:
        rt = (task or {}).get("runtime") or {}
        dec = (task or {}).get("declarative") or {}
        expr = str(dec.get("expression") or "").strip() if task else ""
        cadence = f"{expr} cadence" if expr else "—"
        next_raw = rt.get("next_run_at")
        return (
            f'<div class="job-line-grid"><span class="line-label">{esc(label)}:</span>'
            f'<span class="line-date mono">{esc(_fmt_dt_short(next_raw))}</span>'
            f'<span class="line-mid mono">{esc(cadence)} · {esc(_fmt_from_now(next_raw))}</span>'
            f'<span class="line-status">—</span></div>'
        )

    def row_for(job_row: dict[str, Any]) -> str:
        job_id = str(job_row.get("job_id") or "")
        job_name = str(job_row.get("job_name") or job_id)
        crawl_task_row = job_row.get("crawl") or {}
        link_task_row = job_row.get("link_check") or {}
        history_url = str(job_row.get("history_url") or crawl_task_row.get("history_url") or "").strip()
        crawl_list_u = str(job_row.get("crawl_runs_url") or crawl_task_row.get("crawl_runs_url") or "").strip()
        lc_list_u = str(job_row.get("link_check_runs_url") or link_task_row.get("link_check_runs_url") or "").strip()
        crawl_task = job_row.get("crawl")
        link_task = job_row.get("link_check")
        history_link = f'<a href="{esc(history_url)}">(history)</a>' if history_url else ""
        run_lists = ""
        if crawl_list_u and lc_list_u:
            run_lists = (
                f'<span class="job-run-lists mono"> · <a href="{esc(crawl_list_u)}">crawls</a>'
                f' · <a href="{esc(lc_list_u)}">link-checks</a></span>'
            )
        crawl_task = crawl_task_row
        link_task = link_task_row
        return f"""<tr>
    <td>
      <div class="job-title-row"><span class="job-title">{esc(job_name)}</span><span class="job-history">{history_link}{run_lists}</span></div>
      {_line_last("last crawl", crawl_task)}
      {_line_next("next crawl", crawl_task)}
      {_line_last("last check", link_task)}
      {_line_next("next check", link_task)}
    </td>
  </tr>"""

    rows = "\n".join(row_for(grouped[k]) for k in sorted(grouped.keys())) if grouped else ""
    empty_row = '<tr><td class="empty">No enabled schedule tasks found under jobs_root.</td></tr>'

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
      margin-bottom: 0.75rem;
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
      font-size: 0.9rem;
      font-weight: 600;
      letter-spacing: 0.01em;
      opacity: 0.95;
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
    .job-history a {{
      font-size: 0.8rem;
      color: var(--cardano-blue-light);
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .job-line-grid {{
      display: grid;
      grid-template-columns: 7.25rem 12rem minmax(10rem, 1fr) 8rem;
      gap: 0.75rem;
      align-items: center;
      margin-top: 0.18rem;
      font-size: 0.82rem;
    }}
    .line-label {{
      color: var(--muted);
      font-weight: 600;
    }}
    .line-date {{ white-space: nowrap; }}
    .line-mid {{ color: var(--text); }}
    .line-status {{ white-space: nowrap; }}
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
    footer {{
      margin-top: 2rem;
      padding: 1rem 0;
      font-size: 0.75rem;
      color: var(--muted);
      text-align: center;
    }}
    .jobs-root {{
      word-break: break-all;
      font-size: 0.75rem;
      color: var(--muted);
      margin-top: 0.75rem;
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      {_brand_header(links)}
      <h1>Blink schedules</h1>
      <p>Scheduled crawl and link-check tasks — next run, last run, and status at a glance.</p>
      <nav class="nav" aria-label="Endpoints">
        <a href="{esc(links.get("health", ""))}">Health</a>
        <a href="{esc(links.get("schedule_json", ""))}">Schedule JSON</a>
        <a href="{esc(links.get("slack_health", ""))}">Slack health</a>
        <a href="{esc(links.get("results_index", ""))}">Results</a>
      </nav>
    </div>
  </header>
  <div class="wrap">
    <section class="metrics" aria-label="Summary">
      <div class="metric">
        <div class="value">{total}</div>
        <div class="label">Scheduled tasks</div>
        <div class="hint">Crawl + link-check rows</div>
      </div>
      <div class="metric">
        <div class="value">{crawl_n}</div>
        <div class="label">Crawl</div>
      </div>
      <div class="metric">
        <div class="value">{link_n}</div>
        <div class="label">Link check</div>
      </div>
      <div class="metric">
        <div class="value"><span class="{status_class}">{status_label}</span></div>
        <div class="label">Scheduler thread</div>
        <div class="hint">Started by blink serve</div>
      </div>
    </section>
    <p class="jobs-root"><strong>jobs_root</strong> · {jobs_root}</p>
    <section class="panel" aria-label="Task table">
      <div class="panel-head">Task overview</div>
      <table>
        <thead>
          <tr>
            <th>Job</th>
          </tr>
        </thead>
        <tbody>
          {rows if rows else empty_row}
        </tbody>
      </table>
    </section>
    <footer>Generated {esc(gen_at)} · <a href="{esc(links.get("schedule_refresh", ""))}">Refresh</a></footer>
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
        nav_links=[
            ("Main dashboard", links.get("main_dashboard", "")),
            ("Schedule", links.get("schedule", "")),
            ("Jobs JSON", links.get("jobs_json", "")),
            ("Health", links.get("health", "")),
        ],
        panel_title="Job overview",
        table_headers=["Jobs"],
        table_rows=rows if rows else empty_row,
        footer_link=links.get("refresh", ""),
        footer_label="Refresh",
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
        nav_links=[
            ("Main dashboard", links.get("main_dashboard", "")),
            ("Back to jobs", links.get("jobs_index", "")),
            ("Runs JSON", links.get("runs_json", "")),
            ("Schedule", links.get("schedule", "")),
        ],
        panel_title="Run history",
        table_headers=table_headers,
        table_rows=rows if rows else empty_row,
        footer_link=links.get("refresh", ""),
        footer_label="Refresh",
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
        title=f"Blink structure · {esc(job.get('job_id', ''))} run {esc(run.get('run_id', ''))}",
        heading=f"Structure · {esc(job.get('job_id', ''))}",
        subtitle=f"{esc(job.get('name', ''))} · run {esc(run.get('run_id', ''))}",
        nav_links=[
            ("Main dashboard", links.get("main_dashboard", "")),
            ("Back to run", links.get("run", "")),
            ("Structure JSON", links.get("structure_json", "")),
            ("Jobs", links.get("jobs_index", "")),
        ],
        panel_title="URL path radial tree",
        table_headers=["Field", "Value"],
        table_rows=(
            f"<tr><td>Metric</td><td class=\"mono\">{esc(tree_payload.get('metric') or 'external_count')}</td></tr>"
            f"<tr><td>Node count</td><td class=\"mono\">{esc(tree_payload.get('node_count') or 0)}</td></tr>"
            f"<tr><td>Leaf pages</td><td class=\"mono\">{esc(tree_payload.get('leaf_count') or 0)}</td></tr>"
        ),
        footer_link=links.get("refresh", ""),
        footer_label="Refresh",
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
  <div id="radial-tree" style="width: 100%; overflow: auto; padding: 0 0.5rem 1rem 0.5rem;"></div>
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
  const host = document.getElementById("radial-tree");
  const popover = document.getElementById("node-popover");
  const popoverContent = document.getElementById("node-popover-content");
  const colorBy = document.getElementById("colorBy");
  const sizeBy = document.getElementById("sizeBy");
  const linkCheckRun = document.getElementById("linkCheckRun");
  const externalMode = document.getElementById("externalMode");
  const width = 1000;
  const radius = width / 2;

  function flatten(node, out = []) {{
    out.push(node);
    (node.children || []).forEach((child) => flatten(child, out));
    return out;
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
    const sizeScale = d3.scaleSqrt().domain([0, maxSizeMetric]).range([2.5, 8]);

    const svg = d3.create("svg")
      .attr("viewBox", [-radius, -radius, width, width])
      .attr("width", width)
      .attr("height", width)
      .style("font", "11px sans-serif");

    svg.append("g")
      .attr("fill", "none")
      .attr("stroke", "#cbd5e1")
      .attr("stroke-opacity", 0.8)
      .selectAll("path")
      .data(root.links())
      .join("path")
      .attr("d", d3.linkRadial().angle((d) => d.x).radius((d) => d.y));

    const node = svg.append("g")
      .selectAll("g")
      .data(root.descendants())
      .join("g")
      .attr("transform", (d) => `rotate(${{(d.x * 180 / Math.PI) - 90}}) translate(${{d.y}},0)`);

    node.append("circle")
      .attr("r", (d) => sizeBy.value === "fixed" ? 4 : sizeScale(d.data[keyForSize] || 0))
      .attr("fill", (d) => colorScale(d.data[key] || 0))
      .attr("stroke", "#1e293b")
      .attr("stroke-width", 0.6)
      .style("cursor", "pointer")
      .on("click", (_event, d) => {{
        if (d.data.children && d.data.children.length > 0) {{
          d.data._children = d.data.children;
          d.data.children = [];
        }} else if (d.data._children && d.data._children.length > 0) {{
          d.data.children = d.data._children;
          d.data._children = [];
        }}
        const url = d.data.url || d.data.full_path || "/";
        const kind = d.data.node_kind || "internal";
        const detailsLink = d.data.details_url ? `<a href="${{d.data.details_url}}">Open run details</a>` : "";
        const collapsedState = d.data._children && d.data._children.length > 0 ? "collapsed" : "expanded";
        popoverContent.innerHTML = `
          <div><strong>Path:</strong> <span class="mono">${{url}}</span></div>
          <div><strong>Node kind:</strong> <span class="mono">${{kind}}</span></div>
          <div><strong>External links:</strong> <span class="mono">${{d.data.external_count || 0}}</span></div>
          <div><strong>Failed links:</strong> <span class="mono">${{d.data.failed_count || 0}}</span></div>
          <div><strong>Ignored links:</strong> <span class="mono">${{d.data.ignored_count || 0}}</span></div>
          <div><strong>Node state:</strong> <span class="mono">${{collapsedState}}</span></div>
          <div><strong>Status:</strong> <span class="mono">${{d.data.ok === false ? "failed" : "ok"}}</span></div>
          <div style="margin-top: 0.4rem;">${{detailsLink}}</div>
        `;
        popover.style.display = "block";
        render();
      }});

    node.append("text")
      .attr("dy", "0.31em")
      .attr("x", (d) => d.x < Math.PI === !d.children ? 8 : -8)
      .attr("text-anchor", (d) => d.x < Math.PI === !d.children ? "start" : "end")
      .attr("transform", (d) => d.x >= Math.PI ? "rotate(180)" : null)
      .text((d) => (d.data.node_kind === "external_domain" ? `@${{d.data.name || ""}}` : (d.data.name || "/")))
      .clone(true).lower()
      .attr("stroke", "white");

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


def render_job_task_history_html(
    *,
    job: dict[str, Any],
    crawl_runs: list[dict[str, Any]],
    links: dict[str, str],
) -> str:
    """Render crawl history with related link-check runs under each crawl."""

    def _fmt(value: object) -> str:
        return esc(value if value not in (None, "") else "—")

    rows: list[str] = []
    for crawl in crawl_runs:
        crawl_rows = f"""<tr>
    <td><span class="badge badge-crawl">crawl</span></td>
    <td class="mono"><a href="{esc(crawl.get("details_url", ""))}">{_fmt(crawl.get("run_id"))}</a></td>
    <td class="mono">{_fmt(crawl.get("started_at"))}</td>
    <td class="mono">{_fmt(crawl.get("finished_at") or "ongoing")}</td>
    <td class="mono">{_fmt(crawl.get("pages_visited"))}</td>
    <td class="mono">{_fmt(crawl.get("pages_failed"))}</td>
    <td class="mono">{_html_log_links_cell(crawl)}</td>
    <td class="mono">{_html_report_link_cell(crawl)}</td>
  </tr>"""
        rows.append(crawl_rows)
        for link_run in list(crawl.get("link_checks") or []):
            rows.append(
                f"""<tr>
    <td><span class="badge badge-link_check">link-check</span></td>
    <td class="mono"><a href="{esc(link_run.get("details_url", ""))}">{_fmt(link_run.get("run_id"))}</a></td>
    <td class="mono">{_fmt(link_run.get("started_at"))}</td>
    <td class="mono">{_fmt(link_run.get("finished_at") or "ongoing")}</td>
    <td class="mono">{_fmt(link_run.get("checked_total"))}</td>
    <td class="mono">{_fmt(link_run.get("failed_total"))}</td>
    <td class="mono">{_html_log_links_cell(link_run)}</td>
    <td class="mono">{_html_report_link_cell(link_run)}</td>
  </tr>"""
            )

    return _render_results_shell(
        title=f"Blink history · {esc(job.get('job_id', ''))}",
        heading=f"Task history · {esc(job.get('job_id', ''))}",
        subtitle=f"{esc(job.get('name', ''))} ({esc('enabled' if job.get('enabled') else 'disabled')})",
        nav_links=[
            ("Main dashboard", links.get("main_dashboard", "")),
            ("Back to jobs", links.get("jobs_index", "")),
        ],
        panel_title="Crawl runs with related link-check runs",
        table_headers=["Type", "Run id", "Started", "Finished", "Visited/Checked", "Failed", "Logs", "Report"],
        table_rows="".join(rows) if rows else '<tr><td colspan="8" class="empty">No run history available.</td></tr>',
        footer_link=links.get("refresh", ""),
        footer_label="Refresh",
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
    <td>{esc(item.get("error_message") or "—")}</td>
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
    <td>{esc(item.get("decision_reason") or "—")}</td>
    <td>{esc(item.get("error_message") or "—")}</td>
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
  <table>
    <thead><tr><th>Error category</th>{''.join(f'<th>{esc(label)}</th>' for label in column_labels)}</tr></thead>
    <tbody>{category_rows}</tbody>
  </table>
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
  <table class="sticky-head">
    <thead>
      <tr><th>Target URL</th><th>Status</th><th>Category</th><th>Error</th><th>Checked at</th></tr>
    </thead>
    <tbody>{failed_link_rows}</tbody>
  </table>
</section>
<section class="panel" aria-label="Failed crawl pages" style="margin-top: 1rem;">
  <div class="panel-head">Failed crawl pages</div>
  <table>
    <thead>
      <tr><th>URL</th><th>Depth</th><th>Status</th><th>Error</th><th>Created</th></tr>
    </thead>
    <tbody>{failed_page_rows}</tbody>
  </table>
</section>
<section class="panel" aria-label="Ignored external links" style="margin-top: 1rem;">
  <div class="panel-head">Ignored link-check results (latest per target)</div>
  <table>
    <thead><tr><th>Target URL</th><th>Status</th><th>Category</th><th>Reason</th><th>Error</th><th>Checked at</th></tr></thead>
    <tbody>{ignored_link_rows}</tbody>
  </table>
</section>
"""
    return _render_results_shell(
        title=f"Blink results · {esc(job.get('job_id', ''))} run {esc(run.get('run_id', ''))}",
        heading=f"Run detail · {esc(job.get('job_id', ''))}",
        subtitle=f"{esc(job.get('name', ''))} ({esc('enabled' if job.get('enabled') else 'disabled')})",
        nav_links=[
            ("Main dashboard", links.get("main_dashboard", "")),
            ("Back to job", links.get("job", "")),
            ("Run JSON", links.get("run_json", "")),
            ("Structure", links.get("structure", "")),
            ("Jobs", links.get("jobs_index", "")),
        ],
        panel_title="Overview",
        table_headers=["Field", "Value"],
        table_rows=(
            f"<tr><td>job id</td><td class=\"mono\">{esc(job.get('job_id', ''))}</td></tr>"
            f"<tr><td>job name</td><td>{esc(job.get('name', ''))}</td></tr>"
            f"<tr><td>run start</td><td class=\"mono\">{esc(run.get('started_at') or '—')}</td></tr>"
            f"<tr><td>run end</td><td class=\"mono\">{esc(run.get('finished_at') or '—')}</td></tr>"
            f"<tr><td>run logs</td><td>{_html_log_links_cell(run)}</td></tr>"
        ),
        footer_link=links.get("refresh", ""),
        footer_label="Refresh",
        body_extra=body_extra,
        show_table_header=False,
        branding_links=links,
    )


def _render_results_shell(
    *,
    title: str,
    heading: str,
    subtitle: str,
    nav_links: list[tuple[str, str]],
    panel_title: str,
    table_headers: list[str],
    table_rows: str,
    footer_link: str,
    footer_label: str,
    body_extra: str = "",
    show_table_header: bool = True,
    branding_links: dict[str, str] | None = None,
) -> str:
    branding_links = branding_links or {}
    nav_html = "\n".join(f'<a href="{esc(href)}">{esc(label)}</a>' for label, href in nav_links if href)
    headers_html = "".join(f"<th>{esc(h)}</th>" for h in table_headers)
    gen_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
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
      <nav class="nav" aria-label="Endpoints">
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
    <footer>Generated {esc(gen_at)} · <a href="{esc(footer_link)}">{esc(footer_label)}</a></footer>
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
  .brand { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
  .brand-logo { width: 36px; height: 36px; object-fit: contain; border-radius: 8px; background: rgba(255, 255, 255, 0.14); padding: 4px; }
  .brand-name { font-size: 0.9rem; font-weight: 600; letter-spacing: 0.01em; opacity: 0.95; }
  .job-title { font-weight: 700; margin-bottom: 0.25rem; }
  .job-line { margin-top: 0.2rem; }
  .hero h1 { margin: 0 0 0.35rem; font-size: 1.65rem; font-weight: 700; letter-spacing: -0.02em; }
  .hero p { margin: 0; opacity: 0.92; font-size: 0.95rem; max-width: 42rem; }
  .nav { margin-top: 1rem; display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; font-size: 0.875rem; }
  .nav a { color: #fff; opacity: 0.95; text-decoration: underline; text-underline-offset: 3px; }
  .nav a:hover { opacity: 1; }
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
  .source-col { min-width: 28rem; }
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
