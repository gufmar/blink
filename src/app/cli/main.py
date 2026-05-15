"""Blink command-line entrypoint."""

from __future__ import annotations

import typer

from app.commands.crawl import crawl_app
from app.commands.job import jobs_app
from app.commands.link_check import link_check_app
from app.commands.notifications import notifications_app
from app.commands.schedule import schedule_app
from app.commands.serve import serve_app
from app.commands.user import user_app

app = typer.Typer(help="Blink v3 command line.")
app.add_typer(jobs_app, name="jobs")
app.add_typer(crawl_app, name="crawl")
app.add_typer(link_check_app, name="check")
app.add_typer(notifications_app, name="notifications")
app.add_typer(schedule_app, name="schedule")
app.add_typer(serve_app, name="serve")
app.add_typer(user_app, name="user")


def main() -> None:
    """Module main for `python -m` use."""
    app()


if __name__ == "__main__":
    main()
