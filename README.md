# Be Brown Brave BRIDGE CRM Backend

Backend for the Be Brown Brave BRIDGE CRM. It currently supports daily Walsh Kokosing opportunity scraping, Airtable opportunity upserts, structured run summaries, and a lightweight **internal admin dashboard** for monitoring/manual runs.

> Security warning: `/admin` is intentionally unauthenticated for this internal MVP. Do **not** expose it publicly without adding authentication, IP allowlisting, VPN access, or another access-control layer.

## Current status

### Complete

- Firecrawl-first scraper with direct HTML fallback.
- Parser that skips malformed rows and only processes rows with `Scope Number`.
- Whitespace/markdown normalization and safe handling for non-date strings such as `Deferred`, `TBD`, and `Pending Award`.
- Deterministic multi-category opportunity classification.
- Airtable batch upsert into the Opportunities table by `Scope Number` only.
- Airtable transient retry handling for 408/409/425/429/5xx responses.
- Automatic `Last Scraped` updates on every Airtable payload.
- Automatic retry without `Categories` if the field does not exist yet.
- `GET /health`, `POST /run`, and `GET /admin` endpoints.
- Daily scheduler at `2:00 AM America/New_York`.
- Internal dashboard showing health, scheduler config, Airtable config status, last run summary, scrape path, totals, created/updated/skipped counts, and warnings/errors.

### Intentionally not built yet

- Public customer portal.
- Customer account creation/authentication.
- Public firm intake.
- Customer accept/pass notification workflow.

Airtable remains the main CRM interface. This app is a backend automation/admin monitor, not a replacement CRM.

### Still needed for production CRM phase

- Airtable Firms table write/sync integration.
- Airtable Notifications Logs writes.
- AI firm-opportunity matching and certification priority scoring.
- Email/SMS notification routing with accept/pass links.
- Authentication or admin-only network protection for `/admin`.
- Production persistence if future customer-facing features are added.

## Airtable configuration

Confirmed non-secret identifiers:

```bash
AIRTABLE_BASE_ID=appkRSDtaZ5dzchnZ
AIRTABLE_TABLE_NAME=Opportunities
AIRTABLE_TABLE_ID=tbljlh2uQgSw3uwqy
AIRTABLE_FIRMS_TABLE_ID=tbl63Qw3qlmUv9wFg
AIRTABLE_NOTIFICATIONS_TABLE_ID=tblL1dUryHbiuV3t7
```

Do **not** commit API keys. Set `AIRTABLE_API_KEY` and `FIRECRAWL_API_KEY` through runtime environment variables or a local `.env` file only.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `AIRTABLE_API_KEY` | Yes for Airtable sync | Airtable personal access token. Keep secret. |
| `AIRTABLE_BASE_ID` | Yes | Airtable base ID. Defaults to the confirmed BRIDGE CRM base ID. |
| `AIRTABLE_TABLE_ID` | Recommended | Opportunities table ID. Takes precedence over table name. |
| `AIRTABLE_TABLE_NAME` | Optional | Opportunities table name fallback. Defaults to `Opportunities`. |
| `AIRTABLE_FIRMS_TABLE_ID` | Future phase | Firms table ID for Airtable firm integration. |
| `AIRTABLE_NOTIFICATIONS_TABLE_ID` | Future phase | Notifications Logs table ID for notification logging. |
| `FIRECRAWL_API_KEY` | Optional | Enables Firecrawl scrape path before direct HTML fallback. |
| `BRIDGE_DB_PATH` | Optional | Local SQLite DB path. Defaults to `data/app.db`. |
| `PORT` | Optional | Web server port. Defaults to `8787`. |
| `LOG_LEVEL` | Optional | Python logging level. Defaults to `INFO`. |

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally and add your real secrets there. If using `bash`, load local env vars with:

```bash
set -a
source .env
set +a
```

## Run locally

### One-time opportunity scrape + Airtable upsert

```bash
python src/wk_automation.py --once
```

### Internal admin dashboard

```bash
python src/wk_automation.py --serve --host 0.0.0.0 --port 8787
```

Open:

- `http://localhost:8787/admin`
- `http://localhost:8787/health`

The dashboard includes:

- backend health status
- scheduler configuration
- Airtable base/table config status, without secrets
- last run summary, if available
- scrape path used (`firecrawl`, `direct_html`, or `none`)
- total scraped, created, updated, and skipped counts
- errors/warnings
- a **Run Opportunity Scraper Now** button that calls `POST /run`
- a disabled **Run Matching Now** placeholder for the future matching phase

### Scheduled mode

```bash
python src/wk_automation.py --schedule
```

The scheduler runs daily at `2:00 AM America/New_York`.

## API endpoints

### `GET /health`

Returns service status, timestamp, scheduler configuration, and Airtable config status. It does not expose API keys or secrets.

### `POST /run`

Runs the opportunity scrape + Airtable upsert. Returns a structured JSON summary with scrape path, row counts, created/updated/skipped counts, category-write status, warnings, and errors.

### `GET /admin`

Renders the internal admin dashboard. This page is for operators/admins only and should not be publicly exposed until authentication or network controls are added.

## Deployment: Hostinger VPS

1. SSH into the VPS.
2. Install Python 3.11+ and Git.
3. Clone the repository.
4. Create and activate a virtual environment.
5. Install dependencies.
6. Set environment variables in a systemd service file or a private `.env` outside Git.
7. Run with Gunicorn for the web app.
8. Put `/admin` behind Nginx basic auth, IP allowlisting, VPN, or another admin-only protection before internet exposure.

Example commands:

```bash
git clone <repo-url> bbb-bridge-crm
cd bbb-bridge-crm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gunicorn 'src.wk_automation:create_app()' --bind 0.0.0.0:8787
```

Recommended scheduler options on a VPS:

- Use `python src/wk_automation.py --schedule` as a separate systemd service; or
- Use cron to run `python src/wk_automation.py --once` daily at the equivalent server time for 2:00 AM Eastern.

Health check URL:

```bash
curl http://localhost:8787/health
```

Manual run URL:

```bash
curl -X POST http://localhost:8787/run
```

## Deployment: Render or Railway

Build/install command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn 'src.wk_automation:create_app()' --bind 0.0.0.0:$PORT
```

Add environment variables in the platform dashboard. For scheduled syncs, use the platform scheduler/cron feature to run:

```bash
python src/wk_automation.py --once
```

If deployed on a public app host, do not leave `/admin` publicly reachable without auth or platform-level access controls.

## Troubleshooting

### Scrape returns 0 rows

- Check logs for `Firecrawl failed`, `direct_html`, and `zero-row` messages.
- Confirm the Walsh Kokosing page still exposes a table with `Scope Number` headers.
- Set `FIRECRAWL_API_KEY` so Firecrawl can capture content if direct HTML is incomplete.

### Airtable returns unknown field errors

- `Categories` is optional. The app retries without it automatically.
- Confirm required fields exist in the Opportunities table: `Scope Number`, `Phase`, `Scope Description`, `Price Range`, `Scope Status`, `Source URL`, and `Last Scraped`.

### Airtable duplicate concerns

- Upsert uses `performUpsert` with `Scope Number` as the only merge field.
- The app also de-duplicates duplicate scope numbers within a single scrape before sending to Airtable.

### Date errors

- Date fields are only sent when parseable as ISO dates.
- Non-date strings are logged and skipped for date columns to avoid Airtable date-field rejection.

### Admin dashboard has no last run

- Last run summary is stored in memory for the current server process.
- Click **Run Opportunity Scraper Now** on `/admin` or call `POST /run` to populate it.

## Testing

```bash
python -m py_compile src/wk_automation.py tests/test_wk_automation.py
python -m pytest -q
```

## CRM expansion architecture

The current architecture keeps the opportunity scraper/upserter separate from future customer-facing CRM features. Next production phases should add:

1. `FirmsRepository` for Airtable Firms table reads/writes.
2. `NotificationsRepository` for Airtable Notifications Logs writes.
3. `MatchingService` with AI scoring, certification priority, availability windows, and trade/category fit.
4. Notification adapters for email/SMS with signed accept/pass URLs.
5. Auth or admin-only access so internal pages are protected safely.
