# Be Brown Brave BRIDGE CRM Backend

Backend for the Be Brown Brave BRIDGE CRM. It currently supports daily Walsh Kokosing opportunity scraping, Airtable opportunity upserts, structured run summaries, and a lightweight firm-intake/match-decision web MVP.

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
- `GET /health` and `POST /run` JSON endpoints.
- Daily scheduler at `2:00 AM America/New_York`.
- Local SQLite firm intake + local deterministic match MVP.

### Partially complete

- Customer-facing firm portal exists, but it is still an MVP and should not be considered a full authenticated customer account system.
- Firm matching is deterministic category overlap only; AI matching/scoring is prepared for future expansion but not fully implemented.
- Notifications are not sent yet; accept/pass decisions are stored locally only.

### Still needed for production CRM phase

- Airtable Firms table write/sync integration.
- Airtable Notifications Logs writes.
- AI firm-opportunity matching and certification priority scoring.
- Email/SMS notification routing with accept/pass links.
- Real authentication or magic-link portal access.
- Production database such as Postgres if the customer-facing portal becomes persistent production infrastructure.

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

### Customer-facing web app

```bash
python src/wk_automation.py --serve --host 0.0.0.0 --port 8787
```

Open:

- `http://localhost:8787/`
- `http://localhost:8787/intake`
- `http://localhost:8787/portal?email=firm@example.com`

### Scheduled mode

```bash
python src/wk_automation.py --schedule
```

The scheduler runs daily at `2:00 AM America/New_York`.

## API endpoints

### `GET /health`

Returns service status, timestamp, and scheduler configuration.

### `POST /run`

Runs the opportunity scrape + Airtable upsert, then runs local deterministic matching if the scrape succeeds. Returns a structured JSON summary with scrape path, row counts, created/updated counts, category-write status, and errors.

## Deployment: Hostinger VPS

1. SSH into the VPS.
2. Install Python 3.11+ and Git.
3. Clone the repository.
4. Create and activate a virtual environment.
5. Install dependencies.
6. Set environment variables in a systemd service file or a private `.env` outside Git.
7. Run with Gunicorn for the web app.

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

If the platform does not provide persistent disk, do not rely on the local SQLite firm portal for production. Move firm/match persistence to Airtable or Postgres in the next phase.

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

### Local web portal data disappears

- The current MVP uses SQLite at `data/app.db` by default.
- On ephemeral hosts, use Airtable or Postgres for production persistence in the next phase.

## Testing

```bash
python -m py_compile src/wk_automation.py tests/test_wk_automation.py
python -m pytest -q
```

## CRM expansion architecture

The current architecture keeps the opportunity scraper/upserter separate from local firm intake and deterministic matching. Next production phases should add:

1. `FirmsRepository` for Airtable Firms table reads/writes.
2. `NotificationsRepository` for Airtable Notifications Logs writes.
3. `MatchingService` with AI scoring, certification priority, availability windows, and trade/category fit.
4. Notification adapters for email/SMS with signed accept/pass URLs.
5. Auth or magic-link access so firms can see only their own matches safely.
