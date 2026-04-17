"""HTML for the scheduler dashboard (proxy-safe relative links, no static assets)."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any


def render_schedule_dashboard_html(payload: dict[str, Any]) -> str:
    """Build dashboard HTML. Navigation uses ``../`` so links work behind path prefixes (e.g. ``/blink``)."""
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
        return f"""<tr>
    <td class="mono">{esc(t.get("job_id", ""))}</td>
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
      <h1>Blink schedules</h1>
      <p>Scheduled crawl and link-check tasks — next run, last run, and status at a glance.</p>
      <nav class="nav" aria-label="Endpoints">
        <a href="../health">Health</a>
        <a href="../api/schedule">Schedule JSON</a>
        <a href="../notifications/slack/health">Slack health</a>
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
    <footer>Generated {esc(gen_at)} · <a href="../dashboard">Refresh</a></footer>
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
