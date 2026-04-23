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
blink link-check run --job jobs/cardano.org.job.json --db db.sqlite3
```

Run link checks for a specific crawl run and limit:

```bash
blink link-check run --job jobs/cardano.org.job.json --db db.sqlite3 --run-id 2 --limit 25
```

Control live output in `link-check run`:

```bash
blink link-check run --job jobs/cardano.org.job.json --show-live-failures --show-progress
blink link-check run --job jobs/cardano.org.job.json --hide-live-failures --hide-progress
```

Inspect link-check results (latest check per target URL for a run):

```bash
blink link-check show --job jobs/cardano.org.job.json --run-id 2 --only-failed
```

## Ops Foundation (Step 4)

Default runtime paths are now per job id:

- DB: `jobs/<job_id>/db/<job_id>.sqlite3`
- Logs: `jobs/<job_id>/logs/<yyyy-mm-dd>.log`
- Artifacts: `jobs/<job_id>/artifacts/`

On each crawl or link-check run, missing `db/`, `logs/`, or `artifacts/` folders are created. If the SQLite file is missing (e.g. renamed away), a new empty database with the current schema is created at the canonical path.

After a crawl, the console and log include **internal links skipped by ignore.*** counts per config section (URL-based rules only).

`link_check.http_status` now controls which external-link HTTP status codes are ignored during link-check execution.

Run crawl using default per-job DB path:

```bash
blink crawl run --job jobs/cardano.org.job.json --max-pages 1
```

Run link-check using default per-job DB path:

```bash
blink link-check run --job jobs/cardano.org.job.json --limit 25
```

Optional overrides:

```bash
blink crawl run --job jobs/cardano.org.job.json --db /tmp/custom.sqlite3 --debug
```

## Scheduler (`blink serve`)

Each job’s `schedule` section defines **crawl** and **link-check** tasks (interval or cron). `blink serve` starts Slack routes and a background scheduler that runs `blink crawl run` and `blink link-check run` as **subprocesses** (same as interactive CLI). Job files whose name starts with `_` (such as `_default.job.json`) are not registered.

- Persisted scheduler state: `<jobs-root>/.blink/scheduler.sqlite`
- `GET /api/schedule` — JSON with declarative schedule plus next/last run times
- `GET /dashboard` — schedule UI (summary cards + task table)
- `GET /dashboard/results` — jobs overview with latest run summary
- `GET /dashboard/results/{job_id}` — per-job run history
- `GET /dashboard/results/{job_id}/runs/{run_id}` — per-run details (failed link-check targets + crawl failures + provenance)
- `GET /api/results/jobs` — JSON jobs + latest run summary
- `GET /api/results/jobs/{job_id}/runs` — JSON run history for one job
- `GET /api/results/jobs/{job_id}/runs/{run_id}` — JSON run detail
- Dashboard links are generated via request-aware routes (`url_for`), so navigation remains correct behind reverse proxies and base-path prefixes (for example `https://host/blink/dashboard`).
- `blink schedule show [--jobs-root <dir>] [--job <path>]` — human-readable schedule from disk
- `blink schedule status --url http://127.0.0.1:8080` — status table from a running server
- `blink schedule status --jobs-root <dir>` — combine on-disk jobs with local scheduler state (no HTTP)

Maintenance windows (`schedule.maintenance_windows`) use standard five-field cron strings in `schedule.timezone`. Overlap policy **`skip`** is implemented: if a task is still running, the next tick is skipped.

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

When `link_check.write_json_report` is enabled, each `blink link-check run` writes one JSON report:

- path: `jobs/<job_id>/reports/`
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
- **Slack thread-first lifecycle (Step 12):** when `lifecycle.enabled` is true and `post_alerts_via_bot` is true (default), broken-link alerts are posted with `chat.postMessage` using `bot_token_env`, then a thread bootstrap lists emoji actions and command overrides. `action_aliases.retest` (default `curly_loop`) queues an immediate single-link retest; `blink link-check run` processes the queue at the start of each run and replies in the same thread.
- `blink notifications slack handle-event --job <job.json> --event event.json` applies one Slack `reaction_added` / `message` payload to the job SQLite DB (use for local testing).
- **Slack Events API (Step 14):** `uvicorn` is included in the default install. After `pip install -e .` (or `pip install .` in production), run `blink serve --host 0.0.0.0 --port 8080` (optional `--jobs-root` defaults to `<repo>/jobs`). Configure Slack's Events Request URL to `https://<your-host>/notifications/slack/events`.
- **Routing model:** one Slack workspace, one Blink serve instance, and strict `channel_id -> job` mapping. Each enabled Slack destination channel in job configs must be unique. If duplicate channel IDs are detected across jobs, `blink serve` fails startup with a clear error.
- **Compatibility path:** `/notifications/slack/job/<slug>` still works temporarily for job-specific testing/migration.
- Set the signing secret in the env named by `notifications.slack_signing_secret_env` (default `BLINK_SLACK_SIGNING_SECRET`). TLS is usually handled by a reverse proxy.
- `blink notifications test --job jobs/cardano.org.job.json` sends a greeting/test message with job metadata.
- `blink link-check run` dispatches notifications for newly discovered reportable broken links.
- `blink link-check run --max-blinks 1` limits how many new broken links are notified per run (flood protection).
- Previously reported open links are tracked in DB and not re-notified every run; they are only sent again when reminder timing is due.
- `notifications.crawl_summary_on_run` enables crawl summary messages after each crawl run.
- If a destination credential env var is missing (e.g. webhook URL), dispatch is skipped and logged.
- Legacy top-level `slack` config no longer validates.