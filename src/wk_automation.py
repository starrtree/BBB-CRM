from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from zoneinfo import ZoneInfo

SOURCE_URL = "https://www.walshkokosing.com/bsbc-current-opportunities"
AIRTABLE_BATCH_SIZE = 10
DEFAULT_DB_PATH = Path("data/app.db")

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
    payload = resp.json().get("data", {})
    rows = parse_markdown_table(payload.get("markdown") or "")
    if not rows and payload.get("html"):
        rows = parse_html_table(payload.get("html") or "")
    return rows


def scrape_direct(url: str = SOURCE_URL) -> List[Opportunity]:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return parse_html_table(resp.text)


class AirtableClient:
    def __init__(self, api_key: str, base_id: str, table_name_or_id: str) -> None:
        self.base_url = f"https://api.airtable.com/v0/{base_id}/{table_name_or_id}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        resp = self.session.request(method, url, timeout=30, **kwargs)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp

        import time

        time.sleep(int(resp.headers.get("Retry-After", "2")))
        retry_resp = self.session.request(method, url, timeout=30, **kwargs)
        retry_resp.raise_for_status()
        return retry_resp

    def upsert_batch(self, records: List[Dict[str, object]], merge_field: str = "Scope Number") -> Dict[str, int]:
        created = 0
        updated = 0
        for i in range(0, len(records), AIRTABLE_BATCH_SIZE):
            chunk = records[i : i + AIRTABLE_BATCH_SIZE]
            payload = {
                "performUpsert": {"fieldsToMergeOn": [merge_field]},
                "records": [{"fields": record} for record in chunk],
                "typecast": True,
            }
            response = self._request_with_retry("POST", self.base_url, json=payload)
            body = response.json()
            created_records = body.get("createdRecords", [])
            created += len(created_records)
            updated += max(0, len(chunk) - len(created_records))
        return {"created": created, "updated": updated}


def build_airtable_fields(opp: Opportunity, include_categories: bool = True) -> Dict[str, object]:
    fields: Dict[str, object] = {
        "Scope Number": opp.scope_number,
        "Phase": opp.phase,
        "Scope Description": opp.scope_description,
        "Price Range": opp.price_range,
        "Scope Status": opp.scope_status,
        "Release for Bid": _date_or_text(opp.release_for_bid),
        "Deadline/Quotes Due": _date_or_text(opp.quotes_due),
        "Source URL": opp.source_url,
        "Last Scraped": datetime.now(timezone.utc).isoformat(),
    }
    if include_categories:
        fields["Categories"] = opp.categories or ["Other"]
    if opp.scope_number and opp.scope_description:
        fields["Bid Title"] = f"{opp.scope_number} — {opp.scope_description[:80]}"
    return fields


def run_once() -> Dict[str, int]:
    airtable_key = os.getenv("AIRTABLE_API_KEY", "")
    airtable_base = os.getenv("AIRTABLE_BASE_ID", "")
    airtable_table = os.getenv("AIRTABLE_TABLE_NAME", "Opportunities")
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY", "")

    if not airtable_key or not airtable_base:
        raise RuntimeError("AIRTABLE_API_KEY and AIRTABLE_BASE_ID are required")

    rows = scrape_firecrawl(firecrawl_key) if firecrawl_key else scrape_direct()
    if not rows:
        raise RuntimeError("Scrape returned 0 rows")

    for opp in rows:
        opp.release_for_bid = _date_or_text(opp.release_for_bid)
        opp.quotes_due = _date_or_text(opp.quotes_due)
        opp.categories = categorize(opp)

    client = AirtableClient(airtable_key, airtable_base, airtable_table)
    try:
        result = client.upsert_batch([build_airtable_fields(opp, include_categories=True) for opp in rows])
    except requests.HTTPError as exc:
        message = exc.response.text if exc.response is not None else str(exc)
        if "Unknown field name: \"Categories\"" not in message:
            raise
        result = client.upsert_batch([build_airtable_fields(opp, include_categories=False) for opp in rows])

    return {"total": len(rows), **result}


def ensure_db(path: Path = DEFAULT_DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS firms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            contact_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            capabilities TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_id INTEGER NOT NULL,
            scope_number TEXT NOT NULL,
            categories TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            UNIQUE(firm_id, scope_number),
            FOREIGN KEY(firm_id) REFERENCES firms(id)
        )
        """
    )
    conn.commit()
    conn.close()


def match_firms_to_opportunities(path: Path = DEFAULT_DB_PATH) -> int:
    rows = scrape_firecrawl(os.getenv("FIRECRAWL_API_KEY", "")) if os.getenv("FIRECRAWL_API_KEY") else scrape_direct()
    for opp in rows:
        opp.categories = categorize(opp)

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("SELECT id, capabilities FROM firms")
    firms = cur.fetchall()
    new_matches = 0
    now = datetime.now(timezone.utc).isoformat()

    for firm_id, capabilities in firms:
        cap_tokens = [normalize_text(x).lower() for x in capabilities.split(",") if normalize_text(x)]
        for opp in rows:
            if not opp.scope_number:
                continue
            if any(token in " ".join(opp.categories or []).lower() for token in cap_tokens):
                try:
                    cur.execute(
                        "INSERT INTO matches (firm_id, scope_number, categories, created_at) VALUES (?, ?, ?, ?)",
                        (firm_id, opp.scope_number, ", ".join(opp.categories or ["Other"]), now),
                    )
                    new_matches += 1
                except sqlite3.IntegrityError:
                    pass

    conn.commit()
    conn.close()
    return new_matches


def create_app(db_path: Path = DEFAULT_DB_PATH):
    from flask import Flask, jsonify, redirect, render_template_string, request, url_for

    ensure_db(db_path)
    app = Flask(__name__)

    @app.get("/")
    def home():
        return render_template_string(
            """
            <h1>BBB Opportunity Intake</h1>
            <p>Submit your firm profile for automated bid matching.</p>
            <a href='{{ url_for("intake") }}'>Firm Intake Form</a> | 
            <a href='{{ url_for("portal") }}'>Firm Portal</a>
            """
        )

    @app.route("/intake", methods=["GET", "POST"])
    def intake():
        if request.method == "POST":
            company = normalize_text(request.form.get("company_name", ""))
            contact = normalize_text(request.form.get("contact_name", ""))
            email = normalize_text(request.form.get("email", ""))
            phone = normalize_text(request.form.get("phone", ""))
            capabilities = normalize_text(request.form.get("capabilities", ""))
            if not (company and contact and email and capabilities):
                return "Missing required fields", 400
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO firms (company_name, contact_name, email, phone, capabilities, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (company, contact, email, phone, capabilities, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
            match_firms_to_opportunities(db_path)
            return redirect(url_for("portal", email=email))

        return render_template_string(
            """
            <h2>Firm Intake</h2>
            <form method="post">
              <label>Company Name <input name="company_name" required></label><br/>
              <label>Contact Name <input name="contact_name" required></label><br/>
              <label>Email <input name="email" type="email" required></label><br/>
              <label>Phone <input name="phone"></label><br/>
              <label>Capabilities (comma-separated categories)<br/><textarea name="capabilities" required></textarea></label><br/>
              <button type="submit">Submit</button>
            </form>
            """
        )

    @app.get("/portal")
    def portal():
        email = normalize_text(request.args.get("email", ""))
        if not email:
            return "Provide ?email=you@example.com", 400
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT id, company_name, capabilities FROM firms WHERE email = ?", (email,))
        firm = cur.fetchone()
        if not firm:
            return "Firm not found", 404
        firm_id, company_name, capabilities = firm
        cur.execute("SELECT id, scope_number, categories, status, created_at FROM matches WHERE firm_id = ? ORDER BY created_at DESC", (firm_id,))
        matches = cur.fetchall()
        conn.close()
        return render_template_string(
            """
            <h2>{{ company_name }} Portal</h2>
            <p>Capabilities: {{ capabilities }}</p>
            <h3>Your Matches</h3>
            <ul>
            {% for m in matches %}
              <li>
                Scope {{ m[1] }} | {{ m[2] }} | Status: {{ m[3] }}
                {% if m[3] == 'pending' %}
                  <a href="{{ url_for('decide_match', match_id=m[0], decision='accept') }}">Accept</a>
                  <a href="{{ url_for('decide_match', match_id=m[0], decision='pass') }}">Pass</a>
                {% endif %}
              </li>
            {% endfor %}
            </ul>
            """,
            company_name=company_name,
            capabilities=capabilities,
            matches=matches,
        )

    @app.get("/match/<int:match_id>/<decision>")
    def decide_match(match_id: int, decision: str):
        if decision not in {"accept", "pass"}:
            return "Invalid decision", 400
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE matches SET status = ? WHERE id = ?", (decision, match_id))
        conn.commit()
        conn.close()
        return f"Recorded: {decision}"

    @app.get("/health")
    def health():
        return {"ok": True, "timestamp": datetime.now(timezone.utc).isoformat()}

    @app.post("/run")
    def trigger():
        try:
            scrape_result = run_once()
            match_result = match_firms_to_opportunities(db_path)
            return jsonify({"ok": True, "scrape": scrape_result, "new_matches": match_result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return app


def serve_dashboard(host: str = "0.0.0.0", port: int = 8787) -> None:
    app = create_app()
    app.run(host=host, port=port)


def run_scheduler() -> None:
    scheduler = BlockingScheduler(timezone=ZoneInfo("America/New_York"))
    scheduler.add_job(run_once, "cron", hour=2, minute=0, id="walsh_kokosing_daily")
    scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Walsh Kokosing -> Airtable automation")
    parser.add_argument("--once", action="store_true", help="Run one sync immediately")
    parser.add_argument("--schedule", action="store_true", help="Run 2:00 AM ET daily scheduler")
    parser.add_argument("--serve", action="store_true", help="Start customer-facing web app")
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
