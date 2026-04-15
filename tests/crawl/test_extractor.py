from __future__ import annotations

from app.config.loader import load_effective_job_config
from app.crawl.extractor import IGNORE_SECTION_KEYS, extract_links
from pathlib import Path


def test_extract_links_counts_internal_skips_by_ignore_section() -> None:
    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["target"]["base_url"] = "https://cardano.org"
    config["target"]["allowed_domains"] = ["cardano.org"]
    html = """
    <html><body>
    <a href="/docs/skipme/page">skip internal path</a>
    <a href="https://cardano.org/developers">ok</a>
    </body></html>
    """
    config["ignore"]["path_contains"] = list(config["ignore"]["path_contains"]) + ["skipme"]
    result = extract_links("https://cardano.org/", html, config)
    assert len(result.links) == 1
    assert result.internal_skipped_by_reason["path_contains"] >= 1
    assert result.internal_skipped_by_reason["http_status"] == 0
    for key in IGNORE_SECTION_KEYS:
        assert key in result.internal_skipped_by_reason
