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
    for key in IGNORE_SECTION_KEYS:
        assert key in result.internal_skipped_by_reason


def test_extract_links_keeps_external_anchor_text() -> None:
    config = load_effective_job_config("jobs/cardano.org.job.json", cwd=Path.cwd())
    config["target"]["base_url"] = "https://cardano.org"
    config["target"]["allowed_domains"] = ["cardano.org"]
    html = """
    <html><body>
    <a href="https://ext.example/a">this is the ext URL</a>
    </body></html>
    """
    result = extract_links("https://cardano.org/demo/xyz", html, config)
    assert len(result.links) == 1
    url, is_internal, anchor_text = result.links[0]
    assert url == "https://ext.example/a"
    assert is_internal is False
    assert anchor_text == "this is the ext URL"
