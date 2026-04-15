from __future__ import annotations

from rich.console import Console

from app.runtime.logging import configure_logging, event_logger


def test_configure_logging_writes_file(tmp_path) -> None:
    log_file = tmp_path / "logs" / "2026-04-13.log"
    console = Console(record=True)
    configure_logging(log_file=log_file, console=console, debug=False)

    event_logger("test.event", run_id=123).info("hello from test")

    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8")
    assert "event=test.event" in contents
    assert "run_id=123" in contents
    assert "hello from test" in contents
