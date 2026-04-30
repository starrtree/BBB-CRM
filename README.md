# Walsh Kokosing → Airtable Automation

This repository contains a Python automation service that replaces the original n8n plan for daily sync.

## What it does

- Scrapes `https://www.walshkokosing.com/bsbc-current-opportunities` daily at **2:00 AM America/New_York**.
- Uses **Firecrawl** when `FIRECRAWL_API_KEY` is set; otherwise falls back to direct HTML scraping.
- Parses rows into normalized opportunity records.
- Categorizes opportunities deterministically based on keyword rules.
- Upserts into Airtable idempotently by `Scope Number`.
- Updates `Last Scraped` on every touched row.
- Supports Airtable batch upsert with retry on 429 rate limits.
- Gracefully retries without `Categories` if that field is not present yet.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Populate environment variables:

- `AIRTABLE_API_KEY`
- `AIRTABLE_BASE_ID`
- `AIRTABLE_TABLE_NAME` (name or table ID; defaults to `Opportunities`)
- `FIRECRAWL_API_KEY` (optional but recommended)

## Run modes

### One-time sync

```bash
python src/wk_automation.py --once
```

### Daily scheduler (2:00 AM ET)

```bash
python src/wk_automation.py --schedule
```

### Minimal API/UI surface

```bash
python src/wk_automation.py --serve
```

- `GET /health`
- `POST /run`

## Airtable field expectations

Expected columns in **Opportunities** table:

- `Scope Number` (used as merge key)
- `Phase`
- `Scope Description`
- `Price Range`
- `Scope Status`
- `Release for Bid`
- `Deadline/Quotes Due`
- `Source URL`
- `Last Scraped`

Optional:

- `Categories` (multi-select)
- `Bid Title` (auto-filled as `Scope Number — truncated description` if field exists)

## Notes

- If date parsing fails (`Deferred`, `Pending Award`, etc.), values are sent as plain text.
- If Airtable returns `Unknown field name: "Categories"`, the run retries automatically without that field.
