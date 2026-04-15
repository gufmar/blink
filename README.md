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

After a crawl, the console and log include **internal links skipped by ignore.*** counts per config section. `http_status` is always `0` during link extraction (that rule applies to HTTP responses, not hrefs).

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