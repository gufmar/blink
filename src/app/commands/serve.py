"""`blink serve` — HTTP server for Slack Events API."""

from __future__ import annotations

from pathlib import Path

import typer

serve_app = typer.Typer(help="Run Blink HTTP server (Slack Events API).", invoke_without_command=True)


@serve_app.callback()
def serve_main(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8080, "--port", min=1, max=65535, help="Listen port."),
    jobs_root: Path | None = typer.Option(
        None,
        "--jobs-root",
        help="Directory containing <slug>.job.json files (default: <project>/jobs).",
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
    app = build_app(jobs_root=root)
    typer.secho(
        f"Blink server listening on http://{host}:{port} (jobs_root={root})",
        fg=typer.colors.GREEN,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
