"""Typed models for Blink job configuration."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class MetaConfig(TypedDict):
    job_id: str
    name: str
    description: str
    enabled: bool
    tags: list[str]


class TargetConfig(TypedDict):
    base_url: str
    allowed_domains: list[str]
    start_urls: list[str]
    follow_subdomains: bool


class CrawlTimeoutsConfig(TypedDict):
    navigation_seconds: int
    network_idle_seconds: int
    playwright_wait_seconds: int


class BrowserViewportConfig(TypedDict):
    width: int
    height: int


class CrawlBrowserConfig(TypedDict):
    viewport: BrowserViewportConfig
    locale: str
    timezone_id: str
    extra_http_headers: dict[str, str]
    storage_state_path: str | None
    persist_storage_state: bool
    headless: bool
    block_request_netloc_contains: list[str]


class CrawlObservabilityConfig(TypedDict):
    log_console: bool
    log_non_2xx_responses: bool
    log_request_failures: bool
    save_failure_screenshot: bool
    save_failure_html: bool


class CrawlConfig(TypedDict):
    render_mode: Literal["playwright"]
    user_agent: str
    request_delay_seconds: float
    max_pages_per_run: int
    max_depth: int | None
    parse_querystring: bool
    parse_fragments: bool
    timeouts: CrawlTimeoutsConfig
    retry_count: int
    max_response_bytes: int
    concurrency: int
    browser: NotRequired[CrawlBrowserConfig]
    observability: NotRequired[CrawlObservabilityConfig]


class IgnoreConfig(TypedDict):
    url_schemes: list[str]
    netloc_contains: list[str]
    path_contains: list[str]
    path_extensions: list[str]
    fragment_contains: list[str]


class ContentConfig(TypedDict):
    history_keep: int
    extract_main_text: bool
    store_rendered_html: bool
    main_text_extractor: Literal["regex", "trafilatura"]
    significant_change_threshold_percent: float
    text_compare_max_chars: int


class LinkCheckConfig(TypedDict):
    enabled: bool
    request_timeout_seconds: int
    retry_count: int
    consecutive_failures_before_alert: int
    http_status: list[int]
    follow_redirects: bool
    write_json_report: bool
    save_failure_screenshot: bool
    tolerance: "LinkCheckToleranceConfig"


class LinkCheckToleranceRuleConfig(TypedDict):
    min_consecutive_failures: int
    min_age_days: int


class LinkCheckToleranceByCategoryConfig(TypedDict):
    client: LinkCheckToleranceRuleConfig
    server: LinkCheckToleranceRuleConfig
    timeout: LinkCheckToleranceRuleConfig
    connection: LinkCheckToleranceRuleConfig
    other: LinkCheckToleranceRuleConfig


class LinkCheckToleranceConfig(TypedDict):
    by_category: LinkCheckToleranceByCategoryConfig


class ScheduleTaskConfig(TypedDict):
    enabled: bool
    mode: Literal["interval", "cron"]
    expression: str
    jitter_seconds: int
    max_runtime_seconds: int
    startup_delay_seconds: int


class MaintenanceWindowConfig(TypedDict):
    name: str
    cron: str


class ScheduleConfig(TypedDict):
    timezone: str
    overlap_policy: Literal["skip", "queue", "replace"]
    crawl: ScheduleTaskConfig
    link_check: ScheduleTaskConfig
    maintenance_windows: list[MaintenanceWindowConfig]


class NotificationActionAliasesConfig(TypedDict):
    ignore: str
    claim: str
    on_hold: str
    resolve: str
    retest: str


class SlackLifecyclePolicyConfig(TypedDict, total=False):
    """Slack thread-first lifecycle policy (duration defaults and caps)."""

    enabled: bool
    post_alerts_via_bot: bool
    on_hold_default_days: int
    on_hold_max_days: int
    ignore_default_days: int
    ignore_allow_infinite: bool


class NotificationReminderConfig(TypedDict):
    enabled: bool
    days_after_first_alert: list[int]


class NotificationCapabilitiesConfig(TypedDict):
    supports_threads: bool
    supports_reactions: bool
    supports_interactive_components: bool


class SlackNotificationDestinationConfig(TypedDict):
    type: Literal["slack"]
    id: str
    enabled: bool
    channel_id: str
    webhook_env: str
    bot_token_env: str
    action_aliases: NotificationActionAliasesConfig
    reminders: NotificationReminderConfig
    capabilities: NotRequired[NotificationCapabilitiesConfig]
    lifecycle: NotRequired[SlackLifecyclePolicyConfig]


class NotificationsConfig(TypedDict):
    enabled: bool
    max_blinks_per_run: int
    crawl_summary_on_run: bool
    destinations: list[SlackNotificationDestinationConfig]
    slack_signing_secret_env: NotRequired[str]


class FeaturesConfig(TypedDict):
    ref_counters: list[str]
    netlocs_counted: list[str]


class JobConfig(TypedDict):
    job_version: Literal[1]
    meta: MetaConfig
    target: TargetConfig
    crawl: CrawlConfig
    ignore: IgnoreConfig
    content: ContentConfig
    link_check: LinkCheckConfig
    schedule: ScheduleConfig
    notifications: NotificationsConfig
    features: FeaturesConfig
