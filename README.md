# blink

a crawler that takes care of your website's Broken Links - in a blink

## One-time setup

Start here for fresh machine setup (git clone, apt packages, venv, and browser deps):

- [`install.md`](install.md)

## Blink v3 job config

- JSON Schema: `jobs/job.schema.v1.json`
- Default template: `jobs/_default.job.json`
- Example job: `jobs/cardano.org.job.json`

## CLI bootstrap (Step 1)

Install in editable mode:

```bash
python -m pip install -e ".[dev]"
```

Validate a job:

```bash
blink jobs validate --job jobs/cardano.org.job.json
```

Show effective merged job config (`_default` + override):

```bash
blink jobs show --job jobs/cardano.org.job.json
```

## Crawl MVP (Step 2)

Install browser runtime once:

```bash
playwright install chromium
```

Run a bounded crawl:

```bash
blink crawl run --job jobs/cardano.org.job.json --db db.sqlite3 --max-pages 1
```

## Link-Check MVP (Step 3)

Run link checks against discovered external links from the latest crawl run:

```bash
blink check run --job jobs/cardano.org.job.json --db db.sqlite3
```

Run link checks for a specific crawl run and limit:

```bash
blink check run --job jobs/cardano.org.job.json --db db.sqlite3 --run-id 2 --limit 25
```

Control live output in `check run`:

```bash
blink check run --job jobs/cardano.org.job.json --show-live-failures --show-progress
blink check run --job jobs/cardano.org.job.json --hide-live-failures --hide-progress
```

Inspect link-check results (latest check per target URL for a run):

```bash
blink check show --job jobs/cardano.org.job.json --run-id 2 --only-failed
```

## Ops Foundation (Step 4)

Default runtime paths are now per job id:

- DB: `jobs/data/<job_id>/db/<job_id>.sqlite3`
- Logs: `jobs/data/<job_id>/logs/<yyyy-mm-dd>.log`
- Artifacts: `jobs/data/<job_id>/artifacts/`

On each crawl or link-check run, missing `db/`, `logs/`, or `artifacts/` folders are created. If the SQLite file is missing (e.g. renamed away), a new empty database with the current schema is created at the canonical path.

After a crawl, the console and log include **internal links skipped by crawl.ignore.*** counts per config section (URL-based rules only).

URL behavior is split by concern:

- `crawl.url_normalization.internal.keep_query|keep_fragment` controls internal URL normalization for crawl frontier discovery.
- `crawl.url_normalization.external.store_raw_href` preserves exact external hrefs for persistence/reporting.
- `link_check.target_url_policy.request.keep_query|keep_fragment` controls the URL form used for outbound link-check requests.
- `link_check.ignore.http_status` controls ignored HTTP status codes, and `link_check.ignore.url_schemes` controls ignored URL schemes (for example `mailto`) during link-check execution.
- `link_check.implementation`: `playwright` (default), `http` (urllib only), or `http_then_playwright` (HEAD/preflight + HTTP GET, then Playwright when HTML verification or retry rules apply).
- `link_check.playwright.wait_until`: `commit` (default, fewer false timeouts on slow DOM) or `domcontentloaded`.
- `link_check.playwright.accept_partial_success_on_navigation_timeout`: when `true` (default), if Playwright times out but the main document already returned HTTP 2xx, the link is treated as OK.
- `link_check.playwright.navigation_timeout_seconds|network_idle_seconds|settle_wait_seconds` tunes timing.
- `link_check.playwright.restart_browser_every_n_checks`: after this many completed Playwright checks, Blink closes and reopens Chromium (`0` = never, the default). Use on large jobs to limit memory growth and flaky CDP connections.
- `link_check.preflight`: optional HEAD/GET classification to skip Playwright for archives and other non-HTML responses (`skip_playwright_content_types`, `skip_playwright_path_extensions`).
- `link_check.hybrid`: used by `http_then_playwright` — `retry_playwright_http_status` (e.g. 403/429/503), `retry_playwright_on_connection_error`, when to run Playwright after preflight sees HTML vs unknown `Content-Type`.

Example:

```json
"link_check": {
  "implementation": "playwright",
  "playwright": {
    "navigation_timeout_seconds": 10,
    "network_idle_seconds": 4,
    "settle_wait_seconds": 2,
    "wait_until": "commit",
    "accept_partial_success_on_navigation_timeout": true,
    "restart_browser_every_n_checks": 0
  },
  "preflight": { "enabled": true, "skip_playwright_content_types": [], "skip_playwright_path_extensions": [] },
  "hybrid": {
    "retry_playwright_http_status": [403, 429, 503],
    "retry_playwright_on_connection_error": false,
    "run_playwright_when_preflight_html": true,
    "run_playwright_when_http_ok_unknown_type": false
  }
}
```

`link_check.follow_redirects` applies to urllib-based steps (`http` implementation and preflight/HTTP parts of `http_then_playwright`). Pure Playwright navigation always follows redirects like a browser.

Successful checks may store JSON in `check_meta` on each result (pipeline stage: `preflight`, `http`, `playwright`) for dashboards and JSON reports.

With `http_then_playwright`, average time per URL can rise; increase `schedule.link_check.max_runtime_seconds` if scheduled runs hit the cap.

Run crawl using default per-job DB path:

```bash
blink crawl run --job jobs/cardano.org.job.json --max-pages 1
```

Run link-check using default per-job DB path:

```bash
blink check run --job jobs/cardano.org.job.json --limit 25
```

Optional overrides:

```bash
blink crawl run --job jobs/cardano.org.job.json --db /tmp/custom.sqlite3 --debug
```

## Scheduler (`blink serve`)

Each job’s `schedule` section defines **crawl** and **link-check** tasks (interval or cron). `blink serve` starts Slack routes and a background scheduler that runs `blink crawl run` and `blink check run` as **subprocesses** (same as interactive CLI). Job files whose name starts with `_` (such as `_default.job.json`) are not registered.

- Persisted scheduler state: `<jobs-root>/.blink/scheduler.sqlite`
- `GET /api/schedule` — JSON with declarative schedule plus next/last run times
- `GET /dashboard` — schedule UI (summary cards + task table)
- `GET /dashboard/results` — jobs overview with latest run summary
- `GET /dashboard/results/{job_id}` — per-job run history
- `GET /dashboard/results/{job_id}/runs/{run_id}` — per-run details (start/end, job-wide page/link totals, failed-link category summary, filtered failed results, crawl failures, ignored-link list with source pages)
- `GET /api/results/jobs` — JSON jobs + latest run summary
- `GET /api/results/jobs/{job_id}/runs` — JSON run history for one job
- `GET /api/results/jobs/{job_id}/runs/{run_id}` — JSON run detail
- Dashboard links are generated via request-aware routes and support:
  - proxy-injected root paths (for example `https://host/blink/dashboard`)
  - explicit base path override via `blink serve --base-path /blink` when your proxy does not forward a root path.
- Failed-link filters support include/exclude combinations via query params:
  - `include_status`, `exclude_status`
  - `include_category`, `exclude_category`
- `blink schedule show [--jobs-root <dir>] [--job <path>]` — human-readable schedule from disk
- `blink schedule status --url http://127.0.0.1:8080` — status table from a running server
- `blink schedule status --jobs-root <dir>` — combine on-disk jobs with local scheduler state (no HTTP)

Maintenance windows (`schedule.maintenance_windows`) use standard five-field cron strings in `schedule.timezone`. Overlap policy **`skip`** is implemented: if a task is still running, the next tick is skipped.

## Web UI authentication

When enabled, `/dashboard` and `/api/*` require a signed session cookie. Slack webhook routes (`/notifications/slack/*`) stay on signing-secret verification only.

User accounts and per-job roles live in `<jobs-root>/.blink/server.sqlite` (separate from per-job crawl DBs and `scheduler.sqlite`).

### Enable auth

```bash
export BLINK_AUTH_PASSWORD=1          # email + password login
# export BLINK_AUTH_GOOGLE=1          # optional Google Workspace OIDC
export BLINK_SESSION_SECRET="$(openssl rand -hex 32)"
export BLINK_PUBLIC_BASE_URL="http://127.0.0.1:8080"   # public origin; include mount path if proxied (e.g. https://host/blink)
export BLINK_ROUTE_BASE_PATH=/blink   # optional; must match `blink serve --base-path` (for CLI setup links)
```

Google (optional):

```bash
export BLINK_AUTH_GOOGLE=1
export BLINK_GOOGLE_CLIENT_ID="..."
export BLINK_GOOGLE_CLIENT_SECRET="..."
export BLINK_GOOGLE_ALLOWED_HD="yourcompany.com"   # Workspace hosted domain
```

Register redirect URI: `{BLINK_PUBLIC_BASE_URL}/auth/google/callback`

SMTP (optional — otherwise CLI prints one-time setup/reset tokens):

```bash
export BLINK_SMTP_HOST=smtp.example.com
export BLINK_SMTP_PORT=587
export BLINK_SMTP_USER=...
export BLINK_SMTP_PASSWORD=...
export BLINK_SMTP_FROM="blink@example.com"
```

### One env file for systemd and CLI

Keep secrets in a single root-owned file (example `/etc/blink/blink-serve.env`, mode `640`, group `blink`).

**systemd** (`blink-serve.service`):

```ini
EnvironmentFile=/etc/blink/blink-serve.env
ExecStart=/path/to/blink serve --jobs-root /var/lib/blink/jobs --base-path /blink
```

**CLI** (do not rely on your personal `~/.bashrc` for `BLINK_SESSION_SECRET`):

```bash
sudo blink user check --env-file /etc/blink/blink-serve.env --jobs-root /var/lib/blink/jobs
sudo blink user add admin@yourcompany.com --global-admin \
  --env-file /etc/blink/blink-serve.env --jobs-root /var/lib/blink/jobs
```

Or set `BLINK_ENV_FILE=/etc/blink/blink-serve.env` in your admin shell. `--env-file` overrides shell variables so tokens match the service.

If `source` on the env file fails with “permission denied”, use `--env-file` (Blink reads the file) or:

```bash
sudo bash -c 'set -a; . /etc/blink/blink-serve.env; set +a; blink user check --jobs-root /var/lib/blink/jobs'
```

Setup links fail with “Invalid or expired link” when the CLI **session fingerprint** from `blink user check` does not match what serve uses, or when `--jobs-root` differs.

### Bootstrap first admin (before enabling auth on production)

```bash
blink user add admin@yourcompany.com --global-admin \
  --env-file /etc/blink/blink-serve.env --jobs-root /var/lib/blink/jobs
# note the one-time setup URL/token, set password via /auth/set-password
```

### User CLI

```bash
blink user check [--env-file <path>] [--jobs-root <dir>]
blink user list [--jobs-root <dir>]
blink user add <email> [--global-admin]
blink user delete <email>
blink user set-password <email> --password ...
blink user reset-token <email>
blink user set-global-admin <email> [--enabled/--disabled]
blink user set-job-role <email> <job_id> watcher|solver|job_admin
blink user clear-job-role <email> <job_id>
blink user link-slack <email> <slack_user_id>
```

Roles: **watcher** (read dashboards), **solver** and **job_admin** (reserved for future write actions; read access today), **global admin** (all jobs). One role per job per user.

Rate limiting: `BLINK_AUTH_LOGIN_MAX_ATTEMPTS` (default 8) per IP per `BLINK_AUTH_LOGIN_WINDOW_SECONDS` (default 900). In-memory per process only.

## DB Inspection + Explore (Step 5)

Show recent job run history:

```bash
blink jobs history --job jobs/cardano.org.job.json --limit 20
```

Show crawled pages (with optional substring filter, sort, and depth/status filters):

```bash
blink jobs pages --job jobs/cardano.org.job.json --search "docs/xyz/" --sort-by url --sort-order asc --limit 100
blink jobs pages --job jobs/cardano.org.job.json --max-depth 2 --status-code 200
```

Show currently known external links (numbered rows in table output):

```bash
blink jobs external-links --job jobs/cardano.org.job.json --sort-by seen_count --limit 100
```

Show DB table counts, distinct internal/external URL counts, and human-readable file size:

```bash
blink jobs db-stats --job jobs/cardano.org.job.json
```

Run deep exploration crawl with guardrails (progress logs `external_unique` and `link_rows`):

```bash
blink crawl explore --job jobs/cardano.org.job.json --max-pages 0 --max-runtime-minutes 30 --progress-every 25
```

List commands support `--format json`. Use `--search` or `--search-by` for URL substring matching.

## Maintenance: purging old runs

Use `blink jobs purge` to delete crawl or link-check runs (and their cascaded data) from a single job's SQLite DB. By default the command prints a preview table and a per-table cascade summary, then asks for confirmation. Pass `--yes` to skip the prompt.

```bash
blink jobs purge --job jobs/cardano.org.job.json --task-type crawl --run-id 42
blink jobs purge --job jobs/cardano.org.job.json --task-type crawl --run-id 42 --and-older --yes
blink jobs purge --job jobs/cardano.org.job.json --task-type link-check --run-id 17 --and-older
```

Flags:

- `--task-type {crawl|link-check}` — which run kind `--run-id` refers to.
- `--run-id N` — the row in `crawl_runs` (for `crawl`) or `link_check_runs` (for `link-check`).
- `--and-older` — also delete every run of the same task-type whose id is `<= --run-id`.
- `--yes` — skip the interactive `y/N` confirmation.
- `--db PATH` — operate on a non-default SQLite path.
- `--artifacts-dir PATH` — non-default location for on-disk PNG cleanup.

What gets deleted:

- `crawl` purge cascades through `crawl_pages`, `crawl_links`, `run_pages`, `run_external_links`, `run_page_external_links`, the `run_*_appeared/disappeared` diff tables, every `link_check_runs` row built on top of the deleted crawl run, and their `link_check_results`/`link_check_screenshots` rows.
- `link-check` purge cascades only through that link-check run's `link_check_results` and `link_check_screenshots`. The parent crawl run is left untouched.
- On-disk PNGs referenced by deleted `link_check_screenshots` rows are removed from `jobs/data/<job_id>/artifacts/`.

What survives a purge (job-level state, not bound to runs):

- `link_ignore_rules` — manual ignore rules persist.
- `link_alerts` (including paused/`ignored` Slack lifecycle buckets), `link_alert_events`, `link_failure_state`, `link_retest_queue` — all preserved. Stale `link_alerts.last_reported_run_id` references to deleted crawl runs are NULLed so they don't dangle.
- Master `pages` and `external_links` rows survive (their `*_run_id` columns use `ON DELETE SET NULL`).

## Results retention (dashboard / disk)

Blink does **not** automatically delete old crawl or link-check runs or rotate SQLite databases. History grows until you run `blink jobs purge` (see above) or remove files manually. Purging crawl runs drops per-run rows (`link_check_results`, screenshots, crawl snapshots for those runs, and link-check runs layered on those crawls). Job-level broken-link bookkeeping (`link_alerts`, `link_failure_state`, ignore rules, alert events, retest queue) intentionally survives that purge; stale `link_alerts.last_reported_run_id` pointers are cleared when referenced crawl runs disappear.

The CLI and scheduler append to **daily** log files under `jobs/data/<job_id>/logs/YYYY-MM-DD.log` (use the dashboard log links to open the files for a given run). Optional JSON link-check reports are written under `jobs/data/<job_id>/reports/` when `link_check.write_json_report` is enabled.

## Crawl Hardening (Step 6)

`crawl run` and `crawl explore` now use one shared Playwright browser context per run, so cookies/session state persist across page navigations in that run.

New `crawl.browser` config options (in job config, merged from defaults):

- `viewport.width` / `viewport.height`
- `locale`
- `timezone_id`
- `extra_http_headers`
- `storage_state_path` / `persist_storage_state`
- `headless`
- `block_request_netloc_contains` (abort matching third-party requests before they load)

New `crawl.observability` options:

- `log_console`
- `log_non_2xx_responses`
- `log_request_failures`
- `save_failure_screenshot`
- `save_failure_html`

To disable screenshot artifacts for challenged/non-2xx pages:

```json
"observability": {
  "save_failure_screenshot": false
}
```

Explore/run summaries now also include `challenged`, `non_2xx`, and `request_failures`, and per-page diagnostic events are written into log files (including key Vercel headers when present).

## Normalized Storage + Diffs (Step 7)

Storage is now normalized for new runs:

- Canonical entities: unique `pages` and `external_links`
- Run mappings: `run_pages` and `run_external_links`
- Diffs: appeared/disappeared tables for pages and external links between adjacent runs

Inspect run-to-run changes:

```bash
blink jobs pages-diff --job jobs/cardano.org.job.json --change all --limit 100
blink jobs external-links-diff --job jobs/cardano.org.job.json --change all --limit 100
```

## External link provenance + main text change index

Each crawl records **which internal pages linked to which external URL** in `run_page_external_links` (run + canonical `page_id` + `external_link_id`). List sources for reporting (e.g. broken links):

```bash
blink jobs external-link-sources --job jobs/cardano.org.job.json --target-url "https://example.com/foo"
```

Main body text uses `content.main_text_extractor` (`trafilatura` or `regex`). After each run, `run_pages` stores comparison vs the **previous** finished run for the same job:

- `text_similarity_prev`: `difflib.SequenceMatcher` ratio in 0..1 on whitespace-normalized text (first `content.text_compare_max_chars` characters of each side).
- `text_change_percent_prev`: `(1 - similarity) * 100`.
- `text_significant_change`: 1 when `text_change_percent_prev >= content.significant_change_threshold_percent` (default 25).

Inspect:

```bash
blink jobs pages-content-metrics --job jobs/cardano.org.job.json --only-significant
```

## Link-check JSON reporting (Step 8)

When `link_check.write_json_report` is enabled, each `blink check run` writes one JSON report:

- path: `jobs/data/<job_id>/reports/`
- filename: `report_<job_id>_yyyy-mm-dd_hh-mm.json`

Enable in job config:

```json
"link_check": {
  "write_json_report": true
}
```

Report contents include:

- `meta`: job id, base URL, crawl run id, generated timestamp, crawl/link-check timing
- `summary`: checked/passed/failed/errored/skipped and error category counts
- `errors`: grouped `client`, `server`, `timeout`, `connection`, `other`
- `provenance_stats`: distinct checked targets and distinct source pages referenced

Each error row includes `source_pages` resolved from normalized run provenance (`run_page_external_links`).

Run tests:

```bash
python3 -m pytest -q
```

## Generic notifications (Step 11)

Job config now uses `notifications` (breaking change from legacy `slack` block).

Example:

```json
"notifications": {
  "enabled": true,
  "destinations": [
    {
      "type": "slack",
      "id": "slack-primary",
      "enabled": true,
      "channel_id": "C04HMBZFY9Y",
      "webhook_env": "BLINK_SLACK_WEBHOOK_URL",
      "bot_token_env": "BLINK_SLACK_BOT_TOKEN",
      "action_aliases": {
        "ignore": "see_no_evil",
        "claim": "bust_in_silhouette",
        "on_hold": "double_vertical_bar",
        "resolve": "white_check_mark",
        "retest": "curly_loop"
      },
      "lifecycle": {
        "enabled": true,
        "post_alerts_via_bot": true,
        "on_hold_default_days": 7,
        "on_hold_max_days": 90,
        "ignore_default_days": 30,
        "ignore_allow_infinite": true
      },
      "reminders": {
        "enabled": true,
        "days_after_first_alert": [2, 5, 10]
      }
    }
  ]
}
```

Notes:

- Blink core lifecycle/action handling is destination-agnostic; Slack is the first implemented adapter.
- **Slack thread-first lifecycle (Step 12):** when `lifecycle.enabled` is true and `post_alerts_via_bot` is true (default), broken-link alerts are posted with `chat.postMessage` using `bot_token_env`, then a thread bootstrap lists emoji actions and command overrides. `action_aliases.retest` (default `curly_loop`) queues an immediate single-link retest; `blink check run` processes the queue at the start of each run and replies in the same thread.
- `blink notifications slack handle-event --job <job.json> --event event.json` applies one Slack `reaction_added` / `message` payload to the job SQLite DB (use for local testing).
- **Slack Events API (Step 14):** `uvicorn` is included in the default install. After `pip install -e .` (or `pip install .` in production), run `blink serve --host 0.0.0.0 --port 8080` (optional `--jobs-root` defaults to `<repo>/jobs`). Configure Slack's Events Request URL to `https://<your-host>/notifications/slack/events`.
- **Routing model:** one Slack workspace, one Blink serve instance, and strict `channel_id -> job` mapping. Each enabled Slack destination channel in job configs must be unique. If duplicate channel IDs are detected across jobs, `blink serve` fails startup with a clear error.
- **Compatibility path:** `/notifications/slack/job/<slug>` still works temporarily for job-specific testing/migration.
- Set the signing secret in the env named by `notifications.slack_signing_secret_env` (default `BLINK_SLACK_SIGNING_SECRET`). TLS is usually handled by a reverse proxy.
- `blink notifications test --job jobs/cardano.org.job.json` sends a greeting/test message with job metadata.
- `blink check run` dispatches notifications for newly discovered reportable broken links.
- `blink check run --max-blinks 1` limits how many new broken links are notified per run (flood protection).
- Previously reported open links are tracked in DB and not re-notified every run; they are only sent again when reminder timing is due.
- `notifications.crawl_summary_on_run` enables crawl summary messages after each crawl run.
- If a destination credential env var is missing (e.g. webhook URL), dispatch is skipped and logged.
- Legacy top-level `slack` config no longer validates.