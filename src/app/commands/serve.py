"""`blink serve` — HTTP server for Slack Events API."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from app.config.loader import load_effective_job_config

serve_app = typer.Typer(help="Run Blink HTTP server (Slack Events API).", invoke_without_command=True)


def _mask_env_value(value: str | None) -> str:
    if not value:
        return "<unset>"
    return f"{value[:5]}..."


def _collect_env_vars_for_jobs(jobs_root: Path) -> list[str]:
    """Collect relevant Slack env var names from all job files in jobs_root."""
    names: set[str] = {"BLINK_SLACK_SIGNING_SECRET"}
    for job_path in sorted(jobs_root.glob("*.job.json")):
        try:
            config = load_effective_job_config(job_path)
        except (FileNotFoundError, ValueError):
            continue
        notifications = config.get("notifications") or {}
        signing_name = notifications.get("slack_signing_secret_env")
        if isinstance(signing_name, str) and signing_name.strip():
            names.add(signing_name.strip())
        destinations = notifications.get("destinations") or []
        if not isinstance(destinations, list):
            continue
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            for key in ("webhook_env", "bot_token_env"):
                raw = destination.get(key)
                if isinstance(raw, str) and raw.strip():
                    names.add(raw.strip())
    return sorted(names)


def _emit_env_diagnostics(jobs_root: Path) -> None:
    """Print discovered env vars and masked values (first 5 chars)."""
    names = _collect_env_vars_for_jobs(jobs_root)
    typer.secho("Blink serve environment diagnostics:", fg=typer.colors.CYAN)
    for name in names:
        masked = _mask_env_value(os.getenv(name))
        typer.echo(f"- {name}={masked}")


def _emit_route_registry_diagnostics(channel_routes: dict[str, Path]) -> None:
    typer.secho("Blink serve channel routing registry:", fg=typer.colors.CYAN)
    typer.echo(f"- mapped_channels={len(channel_routes)}")
    for channel_id, path in sorted(channel_routes.items()):
        typer.echo(f"- {channel_id} -> {path.stem.replace('.job', '')}")


@serve_app.callback()
def serve_main(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8080, "--port", min=1, max=65535, help="Listen port."),
    jobs_root: Path | None = typer.Option(
        None,
        "--jobs-root",
        help="Directory containing <slug>.job.json files (default: <project>/jobs).",
    ),
    base_path: str = typer.Option(
        "",
        "--base-path",
        help="Optional URL base path prefix for generated dashboard links (example: /blink).",
    ),
) -> None:
    """Start uvicorn with Slack Events routes under /notifications/slack/."""
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError as exc:
        typer.secho(
            "Missing uvicorn (should be a core dependency). Reinstall Blink: pip install -e .",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    from app.config.loader import project_root  # noqa: PLC0415
    from app.server.asgi import build_app  # noqa: PLC0415

    root = jobs_root.resolve() if jobs_root is not None else project_root() / "jobs"
    _emit_env_diagnostics(root)
    app = build_app(jobs_root=root, enable_scheduler=True, route_base_path=base_path)
    routes = getattr(app.state, "channel_routes", {})
    if isinstance(routes, dict):
        _emit_route_registry_diagnostics(routes)
    typer.secho(
        f"Blink server listening on http://{host}:{port} (jobs_root={root}, base_path={base_path or '/'})",
        fg=typer.colors.GREEN,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
