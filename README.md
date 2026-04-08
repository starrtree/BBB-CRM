# Walsh Kokosing → Airtable Automation

This repo now contains a production-ready Python automation that replaces the original n8n-only concept.

## What it does

- Scrapes `https://www.walshkokosing.com/bsbc-current-opportunities` daily at **2:00 AM America/New_York**.
- Uses **Firecrawl** when `FIRECRAWL_API_KEY` is provided; otherwise falls back to direct HTML scraping.
- Parses opportunities into structured records.
- Normalizes text and safely handles non-date deadline/status values.
- Assigns deterministic, multi-label categories.
- Upserts records into Airtable by `Scope Number`.
- Updates `Last Scraped` on every touched record.
- Includes a tiny API/UI surface for manual trigger (`POST /run`) and health checks (`GET /health`).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then set your credentials in `.env` or environment variables.

## Run modes

### 1) One-time sync

```bash
python src/wk_automation.py --once
```

### 2) Daily scheduled sync (2:00 AM ET)

```bash
python src/wk_automation.py --schedule
```

### 3) Minimal ops endpoint

```bash
python src/wk_automation.py --serve
```

- `GET /health` → service heartbeat
- `POST /run` → trigger a sync now

## What I changed versus your original n8n spec

- Kept your exact pipeline behavior (scrape → parse → categorize → upsert).
- Implemented idempotent Airtable upsert in code instead of n8n graph nodes.
- Added direct scrape fallback so the system still runs if Firecrawl is unavailable.
- Added a small operations API to reduce your manual work.

## Inputs still needed from you

1. Airtable credentials:
   - `AIRTABLE_API_KEY`
   - `AIRTABLE_BASE_ID`
   - `AIRTABLE_TABLE_NAME` (defaults to `Opportunities`)
2. Optional but recommended: `FIRECRAWL_API_KEY`
3. Confirm Airtable field types:
   - `Categories` should be multi-select.
   - `Last Scraped` should support date-time.
   - `Release for Bid` and `Deadline/Quotes Due` can be date or text.

## Suggested next upgrades

- Add stateful run logs (SQLite/Postgres) and alerting (Slack/email) on 0-row scrape.
- Add retry/backoff queue for Airtable 429 responses.
- Add a richer web UI for run history and manual review of parse anomalies.
