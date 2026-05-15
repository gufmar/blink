from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.persistence.sqlite import connect_sqlite, initialize_schema
from app.persistence.sqlite_retry import (
    commit_with_retry,
    is_sqlite_locked_error,
    run_with_sqlite_retry,
)


def test_is_sqlite_locked_error() -> None:
    assert is_sqlite_locked_error(sqlite3.OperationalError("database is locked"))
    assert is_sqlite_locked_error(sqlite3.OperationalError("database table is locked"))
    assert not is_sqlite_locked_error(sqlite3.OperationalError("no such table: x"))
    assert not is_sqlite_locked_error(ValueError("locked"))


def test_commit_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    conn.commit.side_effect = [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("database is locked"),
        None,
    ]
    sleeps: list[float] = []
    monkeypatch.setattr("app.persistence.sqlite_retry.time.sleep", lambda s: sleeps.append(s))
    commit_with_retry(conn, attempts=4, delay_seconds=1.0)
    assert conn.commit.call_count == 3
    assert sleeps == [1.0, 1.0]


def test_commit_raises_after_exhausted_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    conn.commit.side_effect = sqlite3.OperationalError("database is locked")
    monkeypatch.setattr("app.persistence.sqlite_retry.time.sleep", lambda _s: None)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        commit_with_retry(conn, attempts=2, delay_seconds=0.01)


def test_connect_sets_wal_and_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "t.sqlite3"
    conn = connect_sqlite(db_path)
    initialize_schema(conn)
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    busy = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    conn.close()
    assert journal.lower() == "wal"
    assert busy >= 30000


def test_run_with_sqlite_retry_on_contended_db(tmp_path: Path) -> None:
    db_path = tmp_path / "contend.sqlite3"
    conn_a = connect_sqlite(db_path)
    initialize_schema(conn_a)
    conn_a.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    conn_a.commit()
    conn_a.execute("BEGIN IMMEDIATE")
    conn_a.execute("INSERT INTO t(v) VALUES ('hold')")

    attempts = {"n": 0}
    errors: list[BaseException] = []

    def write_b() -> None:
        conn_b = connect_sqlite(db_path)
        try:
            attempts["n"] += 1
            conn_b.execute("INSERT INTO t(v) VALUES ('b')")
            commit_with_retry(conn_b, attempts=4, delay_seconds=0.05)
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn_b.close()

    thread = threading.Thread(target=write_b)
    thread.start()
    time.sleep(0.08)
    conn_a.commit()
    conn_a.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert not errors
    assert attempts["n"] >= 1
    verify = connect_sqlite(db_path)
    row = verify.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    verify.close()
    assert int(row) >= 2
