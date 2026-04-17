from __future__ import annotations

import pytest

from app.schedule.expression import parse_interval_expression


def test_parse_simple_hours() -> None:
    from datetime import timedelta

    assert parse_interval_expression("24h") == timedelta(hours=24)


def test_parse_compound() -> None:
    from datetime import timedelta

    assert parse_interval_expression("1h30m") == timedelta(hours=1, minutes=30)


def test_parse_days() -> None:
    from datetime import timedelta

    assert parse_interval_expression("1d") == timedelta(days=1)


def test_parse_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_interval_expression("")


def test_parse_rejects_junk() -> None:
    with pytest.raises(ValueError):
        parse_interval_expression("24hours")
