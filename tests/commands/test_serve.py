from __future__ import annotations

import json

from app.commands.serve import _collect_env_vars_for_jobs, _mask_env_value


def test_mask_env_value_masks_and_handles_unset() -> None:
    assert _mask_env_value(None) == "<unset>"
    assert _mask_env_value("") == "<unset>"
    assert _mask_env_value("abc") == "abc..."
    assert _mask_env_value("abcdefghi") == "abcde..."


def test_collect_env_vars_for_jobs_reads_job_specific_names(tmp_path) -> None:
    job = tmp_path / "demo.job.json"
    job.write_text(
        json.dumps(
            {
                "notifications": {
                    "slack_signing_secret_env": "MY_SIGNING_SECRET_ENV",
                    "destinations": [
                        {
                            "webhook_env": "MY_WEBHOOK_ENV",
                            "bot_token_env": "MY_BOT_TOKEN_ENV",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    names = _collect_env_vars_for_jobs(tmp_path)
    assert "BLINK_SLACK_SIGNING_SECRET" in names
    assert "MY_SIGNING_SECRET_ENV" in names
    assert "MY_WEBHOOK_ENV" in names
    assert "MY_BOT_TOKEN_ENV" in names
