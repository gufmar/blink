from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.schedule.maintenance import is_start_blocked_by_maintenance


def test_maintenance_blocks_matching_minute() -> None:
    schedule = {
        "timezone": "UTC",
        "overlap_policy": "skip",
        "crawl": {},
        "link_check": {},
        "maintenance_windows": [{"name": "daily", "cron": "30 14 * * *"}],
    }
    at = datetime(2026, 6, 1, 14, 30, tzinfo=ZoneInfo("UTC"))
    assert is_start_blocked_by_maintenance(schedule, at=at) is True


def test_maintenance_allows_other_minutes() -> None:
    schedule = {
        "timezone": "UTC",
        "overlap_policy": "skip",
        "crawl": {},
        "link_check": {},
        "maintenance_windows": [{"name": "daily", "cron": "30 14 * * *"}],
    }
    at = datetime(2026, 6, 1, 14, 31, tzinfo=ZoneInfo("UTC"))
    assert is_start_blocked_by_maintenance(schedule, at=at) is False
