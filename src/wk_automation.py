from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from zoneinfo import ZoneInfo

SOURCE_URL = "https://www.walshkokosing.com/bsbc-current-opportunities"


CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Concrete": ["concrete", "curb", "sidewalk", "slab", "rebar"],
    "Electrical": ["electrical", "conduit", "lighting", "signal"],
    "Landscaping": ["landscape", "erosion", "seeding", "sod", "swppp"],
    "HVAC": ["hvac", "duct", "air handler", "chiller"],
    "Plumbing": ["plumbing", "sanitary", "sewer", "waterline", "pipe"],
    "Demolition": ["demolition", "demo", "removal", "sawcut"],
    "Earthwork": ["excavation", "earthwork", "grading"],
    "Site Work": ["site work", "sitework"],
    "Paving": ["paving", "asphalt"],
    "Utilities": ["utilities", "storm", "drain", "drainage", "water main"],
    "General Construction": ["general", "build", "construct"],
}


@dataclass
class Opportunity:
    scope_number: str
    phase: str = ""
    scope_description: str = ""
    price_range: str = ""
    scope_status: str = ""
    release_for_bid: str = ""
    quotes_due: str = ""
    source_url: str = SOURCE_URL
    categories: Optional[List[str]] = None


def normalize_text(text: str) -> str:
    if not text:
        return ""
    stripped = re.sub(r"[`*_#|]+", " ", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _date_or_text(value: str) -> str:
    value = normalize_text(value)
    if not value:
        return ""
    try:
        dt = date_parser.parse(value, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        return value


def categorize(opportunity: Opportunity) -> List[str]:
    haystack = f"{opportunity.scope_description} {opportunity.phase}".lower()
    matches: List[str] = []

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            matches.append(category)

    return matches or ["Other"]


def parse_markdown_table(md: str) -> List[Opportunity]:
    rows: List[Opportunity] = []
    lines = [line.strip() for line in md.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return rows

    header = [normalize_text(c).lower() for c in lines[0].strip("|").split("|")]
    idx = {name: i for i, name in enumerate(header)}

    def get_col(cols: List[str], *names: str) -> str:
        for name in names:
            if name in idx and idx[name] < len(cols):
                return normalize_text(cols[idx[name]])
        return ""

    for line in lines[2:]:
        cols = [normalize_text(c) for c in line.strip("|").split("|")]
        scope_number = get_col(cols, "scope number")
        if not scope_number:
            continue
        rows.append(
            Opportunity(
                scope_number=scope_number,
                phase=get_col(cols, "phase"),
                scope_description=get_col(cols, "scope description"),
                price_range=get_col(cols, "price range"),
                scope_status=get_col(cols, "scope status"),
                release_for_bid=get_col(cols, "release for bid"),
                quotes_due=get_col(cols, "quotes due", "deadline/quotes due"),
            )
        )
    return rows


def parse_html_table(html: str) -> List[Opportunity]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    headers = [normalize_text(h.get_text()).lower() for h in table.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}

    rows: List[Opportunity] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        cols = [normalize_text(td.get_text(" ")) for td in tds]

        def get_col(*names: str) -> str:
            for name in names:
                if name in idx and idx[name] < len(cols):
                    return cols[idx[name]]
            return ""

        scope_number = get_col("scope number")
        if not scope_number:
            continue
        rows.append(
            Opportunity(
                scope_number=scope_number,
                phase=get_col("phase"),
                scope_description=get_col("scope description"),
                price_range=get_col("price range"),
                scope_status=get_col("scope status"),
                release_for_bid=get_col("release for bid"),
                quotes_due=get_col("quotes due", "deadline/quotes due"),
            )
        )
    return rows


def scrape_firecrawl(api_key: str, url: str = SOURCE_URL) -> List[Opportunity]:
    resp = requests.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"url": url, "formats": ["markdown", "html"]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    payload = data.get("data", {})

    markdown = payload.get("markdown") or ""
    html = payload.get("html") or ""

    rows = parse_markdown_table(markdown)
    if not rows and html:
        rows = parse_html_table(html)
    return rows


def scrape_direct(url: str = SOURCE_URL) -> List[Opportunity]:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return parse_html_table(resp.text)


class AirtableClient:
    def __init__(self, api_key: str, base_id: str, table_name: str) -> None:
        self.base_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def find_by_scope(self, scope_number: str) -> Optional[str]:
        formula = f"{{Scope Number}} = '{scope_number.replace("'", "\\'")}'"
        resp = self.session.get(self.base_url, params={"filterByFormula": formula, "maxRecords": 1}, timeout=30)
        resp.raise_for_status()
        records = resp.json().get("records", [])
        return records[0]["id"] if records else None

    def upsert(self, opp: Opportunity) -> str:
        now_iso = datetime.now(timezone.utc).isoformat()
        fields = {
            "Scope Number": opp.scope_number,
            "Phase": opp.phase,
            "Scope Description": opp.scope_description,
            "Price Range": opp.price_range,
            "Scope Status": opp.scope_status,
            "Release for Bid": _date_or_text(opp.release_for_bid),
            "Deadline/Quotes Due": _date_or_text(opp.quotes_due),
            "Source URL": opp.source_url,
            "Last Scraped": now_iso,
            "Categories": opp.categories or ["Other"],
        }

        existing_id = self.find_by_scope(opp.scope_number)
        if existing_id:
            resp = self.session.patch(f"{self.base_url}/{existing_id}", json={"fields": fields}, timeout=30)
            resp.raise_for_status()
            return "updated"

        resp = self.session.post(self.base_url, json={"fields": fields}, timeout=30)
        resp.raise_for_status()
        return "created"


def run_once() -> Dict[str, int]:
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY", "")
    airtable_key = os.getenv("AIRTABLE_API_KEY", "")
    airtable_base = os.getenv("AIRTABLE_BASE_ID", "")
    airtable_table = os.getenv("AIRTABLE_TABLE_NAME", "Opportunities")

    if not airtable_key or not airtable_base:
        raise RuntimeError("AIRTABLE_API_KEY and AIRTABLE_BASE_ID are required")

    rows = scrape_firecrawl(firecrawl_key) if firecrawl_key else scrape_direct()
    if not rows:
        raise RuntimeError("Scrape returned 0 rows")

    client = AirtableClient(airtable_key, airtable_base, airtable_table)
    created = 0
    updated = 0

    for opp in rows:
        opp.release_for_bid = _date_or_text(opp.release_for_bid)
        opp.quotes_due = _date_or_text(opp.quotes_due)
        opp.categories = categorize(opp)
        result = client.upsert(opp)
        if result == "created":
            created += 1
        else:
            updated += 1

    return {"total": len(rows), "created": created, "updated": updated}


def serve_dashboard(host: str = "0.0.0.0", port: int = 8787) -> None:
    from flask import Flask, jsonify

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return {"ok": True, "timestamp": datetime.now(timezone.utc).isoformat()}

    @app.post("/run")
    def trigger():
        try:
            result = run_once()
            return jsonify({"ok": True, "result": result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    app.run(host=host, port=port)


def run_scheduler() -> None:
    scheduler = BlockingScheduler(timezone=ZoneInfo("America/New_York"))
    scheduler.add_job(run_once, "cron", hour=2, minute=0, id="walsh_kokosing_daily")
    scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Walsh Kokosing -> Airtable automation")
    parser.add_argument("--once", action="store_true", help="Run one sync immediately")
    parser.add_argument("--schedule", action="store_true", help="Run 2:00 AM ET daily scheduler")
    parser.add_argument("--serve", action="store_true", help="Start minimal dashboard/API")
    args = parser.parse_args()

    if args.once:
        print(json.dumps(run_once(), indent=2))
    if args.schedule:
        run_scheduler()
    if args.serve:
        serve_dashboard()
    if not (args.once or args.schedule or args.serve):
        parser.print_help()


if __name__ == "__main__":
    main()
