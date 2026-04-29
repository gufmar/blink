from __future__ import annotations

import json
from pathlib import Path

from app.config.loader import deep_merge, load_effective_job_config
from app.config.schema import validate_job_config


def test_deep_merge_replaces_lists_and_merges_objects() -> None:
    defaults = {
        "meta": {"enabled": True, "tags": ["base"]},
        "features": {"ref_counters": ["a", "b"]},
    }
    overrides = {
        "meta": {"tags": ["prod"]},
        "features": {"ref_counters": ["c"]},
    }

    merged = deep_merge(defaults, overrides)

    assert merged["meta"]["enabled"] is True
    assert merged["meta"]["tags"] == ["prod"]
    assert merged["features"]["ref_counters"] == ["c"]


def test_sample_job_validates() -> None:
    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    issues = validate_job_config(config)
    assert issues == []


def test_effective_config_uses_split_url_policies() -> None:
    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    assert config["crawl"]["url_normalization"]["internal"]["keep_query"] is False
    assert config["crawl"]["url_normalization"]["external"]["store_raw_href"] is True
    assert config["link_check"]["target_url_policy"]["source"] == "external_raw"
    assert config["link_check"]["target_url_policy"]["request"]["keep_query"] is True


def test_invalid_property_fails_validation(tmp_path: Path) -> None:
    job_data = json.loads(Path("jobs/cardano.org.job.json").read_text(encoding="utf-8"))
    job_data["meta"]["unexpected"] = "boom"
    job_path = tmp_path / "invalid.job.json"
    job_path.write_text(json.dumps(job_data), encoding="utf-8")

    config = load_effective_job_config(job_path, cwd=Path.cwd())
    issues = validate_job_config(config)

    assert issues
    assert any("Additional properties are not allowed" in issue.message for issue in issues)


def test_legacy_slack_block_fails_validation(tmp_path: Path) -> None:
    job_data = json.loads(Path("jobs/cardano.org.job.json").read_text(encoding="utf-8"))
    notifications = job_data.pop("notifications")
    assert notifications is not None
    job_data["slack"] = {
        "enabled": True,
        "channel_id": "C123",
        "webhook_env": "BLINK_SLACK_WEBHOOK_URL",
        "bot_token_env": "BLINK_SLACK_BOT_TOKEN",
        "emoji_actions": {"ignore": "x", "claim": "eyes"},
        "reminders": {"enabled": True, "days_after_first_alert": [3]},
    }
    job_path = tmp_path / "legacy-slack.job.json"
    job_path.write_text(json.dumps(job_data), encoding="utf-8")

    config = load_effective_job_config(job_path, cwd=Path.cwd())
    issues = validate_job_config(config)

    assert issues
    assert any(
        ("notifications" in issue.message and "required property" in issue.message)
        or issue.path == "$"
        for issue in issues
    )
