# Walsh Kokosing → Airtable + Firm Matching Web App

This project now includes both:

1. Daily Walsh Kokosing opportunity scraping + Airtable upserts.
2. A lightweight customer-facing web app for firm intake and match decisions.

## Customer-facing flow

- Firm visits the site (`/`) and fills out an intake form (`/intake`) with business/contact/capabilities.
- App stores firm profile in local SQLite.
- App matches capabilities to scraped opportunities by deterministic category overlap.
- Firm can view only their own matches via `/portal?email=...`.
- Firm can click `Accept` or `Pass` per opportunity.

## Automation flow

- Scrape Walsh Kokosing opportunities (Firecrawl preferred, direct HTML fallback).
- Normalize/parse/categorize rows.
- Upsert to Airtable via batch `performUpsert` (Scope Number merge key).
- Retry on Airtable 429 with `Retry-After`.
- If `Categories` field is absent, retry payload without categories.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set environment variables:

- `AIRTABLE_API_KEY`
- `AIRTABLE_BASE_ID`
- `AIRTABLE_TABLE_NAME` (name or table ID)
- `FIRECRAWL_API_KEY` (optional)

## Run

### Run scrape+upsert once

```bash
python src/wk_automation.py --once
```

### Run scheduler (daily 2:00 AM America/New_York)

```bash
python src/wk_automation.py --schedule
```

### Run web app (customer-facing)

```bash
python src/wk_automation.py --serve
```

Routes:

- `GET /` landing page
- `GET/POST /intake` firm intake
- `GET /portal?email=...` customer match portal
- `GET /match/<id>/accept` decision endpoint
- `GET /match/<id>/pass` decision endpoint
- `POST /run` manually trigger scrape + matching
- `GET /health` health status

## Optional no-site alternative

If you prefer a Google Form initially, you can keep this backend and replace `/intake` with a webhook ingest endpoint later.
