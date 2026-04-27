"""HTML for scheduler/results dashboards (proxy-safe and route-aware)."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any


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

    def row_for(t: dict[str, Any]) -> str:
        rt = t.get("runtime") or {}
        dec = t.get("declarative") or {}
        cadence = f'{esc(dec.get("mode", ""))} · {esc(dec.get("expression", ""))}'
        job_id = esc(t.get("job_id", ""))
        results_url = str(t.get("results_url") or "")
        job_cell = (
            f'<a href="{esc(results_url)}" class="mono" title="Open job results">{job_id}</a>'
            if results_url
            else f'<span class="mono">{job_id}</span>'
        )
        return f"""<tr>
    <td>{job_cell}</td>
    <td><span class="badge badge-{esc(t.get("task_type", ""))}">{esc(t.get("task_type", ""))}</span></td>
    <td class="mono cadence">{cadence}</td>
    <td class="mono time">{esc(rt.get("next_run_at") or "—")}</td>
    <td class="mono time">{esc(rt.get("last_end_at") or "—")}</td>
    <td class="mono">{esc(rt.get("last_exit_code") if rt.get("last_exit_code") is not None else "—")}</td>
    <td>{_status_cell(rt)}</td>
  </tr>"""

    rows = "\n".join(row_for(t) for t in sorted(tasks, key=_task_sort_key)) if tasks else ""
    empty_row = '<tr><td colspan="7" class="empty">No enabled schedule tasks found under jobs_root.</td></tr>'

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
            <th>Type</th>
            <th>Cadence</th>
            <th>Next run</th>
            <th>Last end</th>
            <th>Exit</th>
            <th>Status</th>
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
    """Render task-type list + latest run summary."""
    rows_data = list(payload.get("rows") or payload.get("jobs") or [])

    def row_for(row: dict[str, Any]) -> str:
        latest = row.get("latest_run") or {}
        task_type = str(row.get("task_type") or "crawl")
        if task_type == "link_check":
            total = latest.get("checked_total")
            latest_metric = total if total is not None else "—"
        else:
            latest_metric = latest.get("pages_visited") if latest else "—"
        return f"""<tr>
    <td class="mono"><a href="{esc(row.get("details_url", ""))}">{esc(row.get("job_id", ""))}</a></td>
    <td>{esc(row.get("name", ""))}</td>
    <td>{esc(task_type)}</td>
    <td>{esc("yes" if row.get("enabled") else "no")}</td>
    <td class="mono">{esc(latest.get("run_id") if latest else "—")}</td>
    <td class="mono">{esc(latest.get("started_at") if latest else "—")}</td>
    <td class="mono">{esc(latest_metric)}</td>
  </tr>"""

    rows = "\n".join(row_for(row) for row in rows_data) if rows_data else ""
    empty_row = '<tr><td colspan="7" class="empty">No scheduled task rows found.</td></tr>'
    return _render_results_shell(
        title="Blink results · Tasks",
        heading="Blink results",
        subtitle="Browse scheduled crawl/link-check tasks and latest outcomes.",
        nav_links=[
            ("Main dashboard", links.get("main_dashboard", "")),
            ("Schedule", links.get("schedule", "")),
            ("Jobs JSON", links.get("jobs_json", "")),
            ("Health", links.get("health", "")),
        ],
        panel_title="Task results overview",
        table_headers=["Job", "Name", "Task type", "Enabled", "Latest run", "Started", "Visited/Checked"],
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
  </tr>"""
            return f"""<tr>
    <td class="mono"><a href="{esc(run.get("details_url", ""))}">{started}</a></td>
    <td>{esc(row_task_type)}</td>
    <td class="mono">{finished}</td>
    <td class="mono">{esc(run_id)}</td>
    <td class="mono">{esc(run.get("pages_visited") if run.get("pages_visited") is not None else "—")}</td>
    <td class="mono">—</td>
    <td class="mono">{esc(run.get("pages_failed") if run.get("pages_failed") is not None else "—")}</td>
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
  </tr>"""
        return f"""<tr>
    <td class="mono"><a href="{esc(run.get("details_url", ""))}">{started}</a></td>
    <td class="mono">{esc(run_id)}</td>
    <td class="mono">{finished}</td>
    <td class="mono">{esc(run.get("pages_visited"))}</td>
    <td class="mono">—</td>
    <td class="mono">{esc(run.get("pages_failed"))}</td>
  </tr>"""

    rows = "\n".join(row_for(run) for run in run_rows) if run_rows else ""
    empty_colspan = "7" if task_type in {"all", "link_check"} else "6"
    empty_row = f'<tr><td colspan="{empty_colspan}" class="empty">No runs available for this task yet.</td></tr>'
    if task_type == "link_check":
        table_headers = ["Checked at", "Finished", "Link-check run id", "Visited/Checked", "Ignored", "Failed"]
    elif task_type == "all":
        table_headers = ["Started", "Task type", "Finished", "Run id", "Visited/Checked", "Ignored", "Failed"]
    else:
        table_headers = ["Started", "Run id", "Finished", "Visited/Checked", "Ignored", "Failed"]
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
        body_extra=toggle_row,
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
        subtitle=f"Started {esc(run.get('started_at') or '—')} · Finished {esc(run.get('finished_at') or '—')}",
        nav_links=[
            ("Main dashboard", links.get("main_dashboard", "")),
            ("Back to job", links.get("job", "")),
            ("Run JSON", links.get("run_json", "")),
            ("Jobs", links.get("jobs_index", "")),
        ],
        panel_title="Overview",
        table_headers=["Field", "Value"],
        table_rows=(
            f"<tr><td>job id</td><td class=\"mono\">{esc(job.get('job_id', ''))}</td></tr>"
            f"<tr><td>job name</td><td>{esc(job.get('name', ''))}</td></tr>"
            f"<tr><td>run start</td><td class=\"mono\">{esc(run.get('started_at') or '—')}</td></tr>"
            f"<tr><td>run end</td><td class=\"mono\">{esc(run.get('finished_at') or '—')}</td></tr>"
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
        f'<a class="source-link mono" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(url)}</a></span>'
        for url in source_urls
    )
    if not source_rows:
        source_rows = '<span class="mono">—</span>'
    return (
        f'<div class="target-url"><a href="{esc(target_href)}" target="_blank" rel="noopener noreferrer">{esc(target_url)}</a></div>'
        f'<div class="source-list">{source_rows}</div>'
    )


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
