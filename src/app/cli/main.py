"""Blink command-line entrypoint."""

from __future__ import annotations

import typer

from app.commands.crawl import crawl_app
from app.commands.job import jobs_app
from app.commands.link_check import link_check_app

app = typer.Typer(help="Blink v3 command line.")
app.add_typer(jobs_app, name="jobs")
app.add_typer(crawl_app, name="crawl")
app.add_typer(link_check_app, name="link-check")


def main() -> None:
    """Module main for `python -m` use."""
    app()


if __name__ == "__main__":
    main()
