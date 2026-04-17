"""`blink schedule` — inspect job schedules and runtime status."""

from __future__ import annotations

from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from app.config.loader import load_effective_job_config, project_root
from app.config.schema import validate_job_config
from app.schedule.humanize import describe_schedule
from app.schedule.service import BlinkSchedulerService

schedule_app = typer.Typer(help="Inspect crawl/link-check schedules (declarative and runtime).")
_console = Console()


def _jobs_root(default: Path | None = None) -> Path:
    return default if default is not None else project_root() / "jobs"


def _slug_from_path(job_path: Path) -> str:
    stem = job_path.stem
    return stem.removesuffix(".job") if stem.endswith(".job") else stem


def _describe_job_schedule_table(title: str, rows: list[tuple[str, str]]) -> Table:
    t = Table(title=title, show_header=True, header_style="bold")
    t.add_column("Field", style="cyan", no_wrap=True)
    t.add_column("Value")
    for key, val in rows:
        t.add_row(key, val)
    return t


def _gather_job_paths(job: Path | None, jobs_root: Path | None) -> list[Path]:
    root = jobs_root.resolve() if jobs_root is not None else _jobs_root(None)
    if job is not None:
        return [job.resolve()]
    paths = sorted(root.glob("*.job.json"))
    return [p for p in paths if not p.name.startswith("_")]


@schedule_app.command("show")
def schedule_show(
    job: Path | None = typer.Option(None, "--job", help="Single job JSON file."),
    jobs_root: Path | None = typer.Option(
        None,
        "--jobs-root",
        help="Directory of *.job.json (used when --job is omitted). Default: <project>/jobs.",
    ),
) -> None:
    """Print declarative schedule sections from job file(s)."""
    paths = _gather_job_paths(job, jobs_root)
    if not paths:
        typer.secho("No job files found.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    for jp in paths:
        try:
            cfg = load_effective_job_config(jp)
        except (FileNotFoundError, ValueError, OSError) as exc:
            typer.secho(f"{jp}: failed to load ({exc})", fg=typer.colors.RED, err=True)
            continue
        issues = validate_job_config(cfg)
        if issues:
            typer.secho(f"{jp}: invalid config — {issues[0].message}", fg=typer.colors.RED, err=True)
            continue
        slug = _slug_from_path(jp)
        jid = cfg["meta"]["job_id"]
        rows = describe_schedule(cfg["schedule"])
        table = _describe_job_schedule_table(f"{slug} ({jid})", rows)
        _console.print(table)
        _console.print()


@schedule_app.command("status")
def schedule_status(
    url: str | None = typer.Option(
        None,
        "--url",
        help="blink serve base URL (e.g. http://127.0.0.1:8080). If set, fetch /api/schedule.",
    ),
    jobs_root: Path | None = typer.Option(
        None,
        "--jobs-root",
        help="Without --url: load declarative config + scheduler.sqlite under this directory.",
    ),
    view: str = typer.Option(
        "timeline",
        "--view",
        help="timeline (single sorted list) or separated (crawl vs link-check blocks).",
    ),
) -> None:
    """Show runtime schedule status (next/last run) from serve or local state DB."""
    if url:
        base = url.rstrip("/")
        try:
            r = httpx.get(f"{base}/api/schedule", timeout=30.0)
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPError as exc:
            typer.secho(f"HTTP error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    else:
        root = jobs_root.resolve() if jobs_root is not None else _jobs_root(None)
        svc = BlinkSchedulerService(root)
        payload = svc.build_schedule_payload()

    _render_status_payload(payload, view=view)


def _render_status_payload(payload: dict, *, view: str) -> None:
    jr = str(payload.get("jobs_root", ""))
    sr = payload.get("scheduler_running")
    typer.echo(f"jobs_root={jr}")
    typer.echo(f"scheduler_running={sr}")

    tasks: list[dict] = list(payload.get("tasks") or [])
    crawl = list(payload.get("crawl_tasks") or [])
    link = list(payload.get("link_check_tasks") or [])

    def one_table(title: str, rows: list[dict]) -> None:
        t = Table(title=title, show_header=True, header_style="bold")
        t.add_column("job_id")
        t.add_column("task")
        t.add_column("next_run_at")
        t.add_column("last_end_at")
        t.add_column("exit")
        t.add_column("running")
        for row in rows:
            rt = row.get("runtime") or {}
            t.add_row(
                str(row.get("job_id", "")),
                str(row.get("task_type", "")),
                str(rt.get("next_run_at") or "-"),
                str(rt.get("last_end_at") or "-"),
                str(rt.get("last_exit_code") if rt.get("last_exit_code") is not None else "-"),
                str(rt.get("running", False)),
            )
        _console.print(t)

    if view == "separated":
        one_table("Crawl schedules", crawl)
        one_table("Link-check schedules", link)
    else:
        one_table("All scheduled tasks", tasks)
