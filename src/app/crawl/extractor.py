"""URL extraction and filtering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.models.job_config import JobConfig

# All ignore.* sections from job config (http_status applies to fetched resources, not href parsing).
IGNORE_SECTION_KEYS: tuple[str, ...] = (
    "http_status",
    "url_schemes",
    "netloc_contains",
    "path_contains",
    "path_extensions",
    "fragment_contains",
)


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value.strip())


def extract_hrefs(html: str) -> list[str]:
    parser = _HrefParser()
    parser.feed(html)
    return parser.hrefs


def normalize_url(url: str, parse_querystring: bool, parse_fragments: bool) -> str:
    parts = urlsplit(url)
    query = parts.query if parse_querystring else ""
    fragment = parts.fragment if parse_fragments else ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, fragment))


def is_internal_url(url: str, config: JobConfig) -> bool:
    host = urlsplit(url).netloc.lower()
    allowed = {domain.lower() for domain in config["target"]["allowed_domains"]}
    if host in allowed:
        return True
    if config["target"]["follow_subdomains"]:
        return any(host.endswith(f".{domain}") for domain in allowed)
    return False


def ignore_reason(url: str, config: JobConfig) -> str | None:
    """Return the first matching ignore.* section, or None if URL is not ignored."""
    ignore = config["ignore"]
    split = urlsplit(url)
    scheme = split.scheme.lower()
    host = split.netloc.lower()
    path = split.path or "/"
    fragment = split.fragment

    if scheme in {s.lower() for s in ignore["url_schemes"]}:
        return "url_schemes"
    if any(part.lower() in host for part in ignore["netloc_contains"]):
        return "netloc_contains"
    if any(part in path for part in ignore["path_contains"]):
        return "path_contains"
    if any(path.lower().endswith(ext.lower()) for ext in ignore["path_extensions"]):
        return "path_extensions"
    if any(part in fragment for part in ignore["fragment_contains"]):
        return "fragment_contains"
    return None


def should_ignore_url(url: str, config: JobConfig) -> bool:
    return ignore_reason(url, config) is not None


def empty_ignore_skip_counts() -> dict[str, int]:
    return {key: 0 for key in IGNORE_SECTION_KEYS}


@dataclass(frozen=True)
class ExtractLinksResult:
    links: list[tuple[str, bool]]
    """Pairs of (normalized_url, is_internal) for links that pass ignore rules."""

    internal_skipped_by_reason: dict[str, int]
    """Counts of *internal* candidate URLs skipped per ignore section (href phase; http_status always 0)."""


def extract_links(source_url: str, html: str, config: JobConfig) -> ExtractLinksResult:
    """Extract links; count internal URLs dropped by each ignore rule."""
    links: list[tuple[str, bool]] = []
    skipped: dict[str, int] = empty_ignore_skip_counts()
    seen: set[str] = set()
    for href in extract_hrefs(html):
        absolute = urljoin(source_url, href)
        normalized = normalize_url(
            absolute,
            parse_querystring=config["crawl"]["parse_querystring"],
            parse_fragments=config["crawl"]["parse_fragments"],
        )
        reason = ignore_reason(normalized, config)
        if reason:
            if is_internal_url(normalized, config):
                skipped[reason] += 1
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append((normalized, is_internal_url(normalized, config)))
    return ExtractLinksResult(links=links, internal_skipped_by_reason=skipped)
