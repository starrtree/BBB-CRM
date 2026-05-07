from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

SOURCE_URL = "https://www.walshkokosing.com/bsbc-current-opportunities"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
AIRTABLE_API_ROOT = "https://api.airtable.com/v0"
AIRTABLE_BATCH_SIZE = 10
DEFAULT_DB_PATH = Path(os.getenv("BRIDGE_DB_PATH", "data/app.db"))
SCHEDULER_TIMEZONE = "America/New_York"
SCHEDULER_HOUR = 2
SCHEDULER_MINUTE = 0
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

logger = logging.getLogger("bbb_bridge_crm")

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

HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "scope_number": ("scope number", "scope #", "scope no", "scope"),
    "phase": ("phase",),
    "scope_description": ("scope description", "description", "scope desc"),
    "price_range": ("price range", "range", "budget"),
    "scope_status": ("scope status", "status"),
    "release_for_bid": ("release for bid", "released for bid", "release date"),
    "quotes_due": ("quotes due", "deadline/quotes due", "deadline", "due date", "bid due"),
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


@dataclass
class ScrapeResult:
    rows: List[Opportunity]
    path_used: str
    attempted_paths: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class RunSummary:
    ok: bool
    total_parsed: int = 0
    scrape_path: str = "none"
    created: int = 0
    updated: int = 0
    categories_written: bool = True
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""
    errors: List[str] = field(default_factory=list)

    def finish(self) -> Dict[str, object]:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        return asdict(self)


@dataclass(frozen=True)
class AirtableConfig:
    base_id: str
    opportunities_table: str
    firms_table: str = ""
    notifications_table: str = ""

    @classmethod
    def from_env(cls) -> "AirtableConfig":
        return cls(
            base_id=os.getenv("AIRTABLE_BASE_ID", "appkRSDtaZ5dzchnZ"),
            opportunities_table=os.getenv("AIRTABLE_TABLE_ID") or os.getenv("AIRTABLE_TABLE_NAME", "Opportunities"),
            firms_table=os.getenv("AIRTABLE_FIRMS_TABLE_ID", "tbl63Qw3qlmUv9wFg"),
            notifications_table=os.getenv("AIRTABLE_NOTIFICATIONS_TABLE_ID", "tblL1dUryHbiuV3t7"),
        )


def configure_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def normalize_text(text: object) -> str:
    if text is None:
        return ""
    stripped = re.sub(r"[`*_#|]+", " ", str(text))
    stripped = stripped.replace("\xa0", " ")
    return re.sub(r"\s+", " ", stripped).strip()


def parse_date_iso(value: str) -> Optional[str]:
    value = normalize_text(value)
    if not value or value.lower() in {"deferred", "pending", "pending award", "tbd", "n/a", "na", "-"}:
        return None

    try:
        from dateutil import parser as date_parser

        return date_parser.parse(value, fuzzy=True).date().isoformat()
    except Exception:
        logger.info("Skipping non-parseable date value: %s", value)
        return None


def _date_or_text(value: str) -> str:
    return parse_date_iso(value) or normalize_text(value)


def categorize(opportunity: Opportunity) -> List[str]:
    haystack = f"{opportunity.scope_description} {opportunity.phase}".lower()
    matches: List[str] = []

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            matches.append(category)

    if not matches:
        logger.info("No category keywords matched for scope %s; assigning Other", opportunity.scope_number)
    return matches or ["Other"]


def canonical_header(text: object) -> str:
    header = normalize_text(text).lower()
    header = re.sub(r"\s*/\s*", "/", header)
    return header


def _header_index(headers: Sequence[str]) -> Dict[str, int]:
    normalized = [canonical_header(header) for header in headers]
    index: Dict[str, int] = {}
    for field_name, aliases in HEADER_ALIASES.items():
        canonical_aliases = [canonical_header(alias) for alias in aliases]
        for position, header in enumerate(normalized):
            if header in canonical_aliases:
                index[field_name] = position
                break
    return index


def _get_cell(cells: Sequence[str], index: Dict[str, int], field_name: str) -> str:
    position = index.get(field_name)
    if position is None or position >= len(cells):
        return ""
    return normalize_text(cells[position])


def _opportunity_from_cells(cells: Sequence[str], index: Dict[str, int]) -> Optional[Opportunity]:
    scope_number = _get_cell(cells, index, "scope_number")
    if not scope_number:
        logger.debug("Skipping malformed row without Scope Number: %s", cells)
        return None

    return Opportunity(
        scope_number=scope_number,
        phase=_get_cell(cells, index, "phase"),
        scope_description=_get_cell(cells, index, "scope_description"),
        price_range=_get_cell(cells, index, "price_range"),
        scope_status=_get_cell(cells, index, "scope_status"),
        release_for_bid=_get_cell(cells, index, "release_for_bid"),
        quotes_due=_get_cell(cells, index, "quotes_due"),
    )


def parse_markdown_table(md: str) -> List[Opportunity]:
    rows: List[Opportunity] = []
    lines = [line.strip() for line in md.splitlines() if line.strip().startswith("|")]

    i = 0
    while i < len(lines) - 1:
        headers = [normalize_text(cell) for cell in lines[i].strip("|").split("|")]
        index = _header_index(headers)
        separator_candidate = lines[i + 1]
        is_separator = bool(re.match(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$", separator_candidate))

        if "scope_number" not in index or not is_separator:
            i += 1
            continue

        i += 2
        while i < len(lines):
            cells = [normalize_text(cell) for cell in lines[i].strip("|").split("|")]
            if len(cells) < 2:
                i += 1
                continue
            opportunity = _opportunity_from_cells(cells, index)
            if opportunity:
                rows.append(opportunity)
            i += 1
        break

    logger.info("Parsed %s opportunities from markdown", len(rows))
    return rows


class TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._current_table: Optional[List[List[str]]] = None
        self._current_row: Optional[List[str]] = None
        self._current_cell: Optional[List[str]] = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"th", "td"} and self._current_row is not None:
            self._current_cell = []
            self._in_cell = True

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._current_row is not None and self._current_cell is not None:
            self._current_row.append(normalize_text(" ".join(self._current_cell)))
            self._current_cell = None
            self._in_cell = False
        elif tag == "tr" and self._current_table is not None and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            self.tables.append(self._current_table)
            self._current_table = None


def parse_html_table(html: str) -> List[Opportunity]:
    parser = TableHTMLParser()
    parser.feed(html or "")
    rows: List[Opportunity] = []

    for table in parser.tables:
        if not table:
            continue
        index = _header_index(table[0])
        if "scope_number" not in index:
            continue
        for cells in table[1:]:
            opportunity = _opportunity_from_cells(cells, index)
            if opportunity:
                rows.append(opportunity)
        if rows:
            break

    logger.info("Parsed %s opportunities from HTML", len(rows))
    return rows


def _requests_session():
    import requests

    return requests.Session()


def scrape_firecrawl(api_key: str, url: str = SOURCE_URL) -> List[Opportunity]:
    session = _requests_session()
    response = session.post(
        FIRECRAWL_SCRAPE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"url": url, "formats": ["markdown", "html"]},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json().get("data", {})
    rows = parse_markdown_table(payload.get("markdown") or "")
    if not rows and payload.get("html"):
        logger.warning("Firecrawl markdown parse produced 0 rows; trying Firecrawl HTML payload")
        rows = parse_html_table(payload.get("html") or "")
    return rows


def scrape_direct(url: str = SOURCE_URL) -> List[Opportunity]:
    session = _requests_session()
    response = session.get(url, timeout=60, headers={"User-Agent": "BBB-BRIDGE-CRM/1.0"})
    response.raise_for_status()
    return parse_html_table(response.text)


def scrape_opportunities(url: str = SOURCE_URL, firecrawl_key: str | None = None) -> ScrapeResult:
    errors: List[str] = []
    attempted_paths: List[str] = []

    if firecrawl_key:
        attempted_paths.append("firecrawl")
        try:
            rows = scrape_firecrawl(firecrawl_key, url)
            if rows:
                logger.info("Scrape succeeded via Firecrawl with %s rows", len(rows))
                return ScrapeResult(rows=rows, path_used="firecrawl", attempted_paths=attempted_paths, errors=errors)
            errors.append("Firecrawl returned 0 parsed rows")
            logger.warning("Firecrawl returned 0 parsed rows; falling back to direct HTML")
        except Exception as exc:
            errors.append(f"Firecrawl failed: {exc}")
            logger.exception("Firecrawl scrape failed; falling back to direct HTML")

    attempted_paths.append("direct_html")
    try:
        rows = scrape_direct(url)
        if rows:
            logger.info("Scrape succeeded via direct HTML with %s rows", len(rows))
            return ScrapeResult(rows=rows, path_used="direct_html", attempted_paths=attempted_paths, errors=errors)
        errors.append("Direct HTML returned 0 parsed rows")
        logger.error("Direct HTML returned 0 parsed rows")
    except Exception as exc:
        errors.append(f"Direct HTML failed: {exc}")
        logger.exception("Direct HTML scrape failed")

    return ScrapeResult(rows=[], path_used="none", attempted_paths=attempted_paths, errors=errors)


class AirtableClient:
    def __init__(self, api_key: str, base_id: str, table_name_or_id: str, max_retries: int = 3) -> None:
        safe_table = quote(table_name_or_id, safe="")
        self.base_url = f"{AIRTABLE_API_ROOT}/{base_id}/{safe_table}"
        self.session = _requests_session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        self.max_retries = max_retries

    def _request_with_retry(self, method: str, url: str, **kwargs):
        last_response = None
        for attempt in range(self.max_retries + 1):
            response = self.session.request(method, url, timeout=30, **kwargs)
            last_response = response
            if response.status_code not in TRANSIENT_STATUS_CODES:
                response.raise_for_status()
                return response

            sleep_for = int(response.headers.get("Retry-After", "0")) or min(2**attempt, 8)
            logger.warning(
                "Transient Airtable failure status=%s attempt=%s/%s retry_in=%ss body=%s",
                response.status_code,
                attempt + 1,
                self.max_retries + 1,
                sleep_for,
                response.text[:500],
            )
            if attempt < self.max_retries:
                time.sleep(sleep_for)

        assert last_response is not None
        last_response.raise_for_status()
        return last_response

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
            try:
                response = self._request_with_retry("PATCH", self.base_url, json=payload)
                body = response.json()
                created_records = body.get("createdRecords", [])
                created += len(created_records)
                updated += max(0, len(chunk) - len(created_records))
                logger.info("Airtable upsert chunk complete created=%s updated=%s", len(created_records), max(0, len(chunk) - len(created_records)))
            except Exception:
                logger.exception("Airtable upsert chunk failed for %s records", len(chunk))
                raise
        return {"created": created, "updated": updated}


def build_airtable_fields(opp: Opportunity, include_categories: bool = True, include_unparseable_dates: bool = False) -> Dict[str, object]:
    fields: Dict[str, object] = {
        "Scope Number": opp.scope_number,
        "Phase": opp.phase,
        "Scope Description": opp.scope_description,
        "Price Range": opp.price_range,
        "Scope Status": opp.scope_status,
        "Source URL": opp.source_url,
        "Last Scraped": datetime.now(timezone.utc).isoformat(),
    }

    for airtable_field, raw_value in (
        ("Release for Bid", opp.release_for_bid),
        ("Deadline/Quotes Due", opp.quotes_due),
    ):
        parsed_date = parse_date_iso(raw_value)
        if parsed_date:
            fields[airtable_field] = parsed_date
        elif include_unparseable_dates and normalize_text(raw_value):
            fields[airtable_field] = normalize_text(raw_value)
        else:
            logger.info("Skipping %s for scope %s because value is not an ISO-parseable date", airtable_field, opp.scope_number)

    if include_categories:
        fields["Categories"] = opp.categories or ["Other"]
    if opp.scope_number and opp.scope_description:
        fields["Bid Title"] = f"{opp.scope_number} — {opp.scope_description[:80]}"
    return fields


def airtable_error_text(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    return getattr(response, "text", "") or str(exc)


def _airtable_upsert_with_field_fallback(client: AirtableClient, rows: List[Opportunity]) -> Tuple[Dict[str, int], bool]:
    try:
        return client.upsert_batch([build_airtable_fields(opp, include_categories=True) for opp in rows]), True
    except Exception as exc:
        message = airtable_error_text(exc)
        if "Unknown field name: \"Categories\"" not in message and "UNKNOWN_FIELD_NAME" not in message:
            raise
        logger.warning("Airtable Categories field is unavailable; retrying upsert without Categories")
        return client.upsert_batch([build_airtable_fields(opp, include_categories=False) for opp in rows]), False


def run_once() -> Dict[str, object]:
    summary = RunSummary(ok=False)
    airtable_key = os.getenv("AIRTABLE_API_KEY", "")
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY", "")
    config = AirtableConfig.from_env()

    if not airtable_key:
        summary.errors.append("AIRTABLE_API_KEY is required")
        logger.error("AIRTABLE_API_KEY is required")
        return summary.finish()

    scrape = scrape_opportunities(firecrawl_key=firecrawl_key)
    summary.scrape_path = scrape.path_used
    summary.errors.extend(scrape.errors)
    if not scrape.rows:
        summary.errors.append("Scrape returned 0 rows")
        logger.error("Run failed because scraper produced 0 rows. attempted_paths=%s errors=%s", scrape.attempted_paths, scrape.errors)
        return summary.finish()

    seen_scope_numbers = set()
    rows: List[Opportunity] = []
    for opp in scrape.rows:
        if not opp.scope_number:
            logger.warning("Skipping parsed opportunity without Scope Number: %s", opp)
            continue
        if opp.scope_number in seen_scope_numbers:
            logger.warning("Skipping duplicate scraped Scope Number before Airtable upsert: %s", opp.scope_number)
            continue
        seen_scope_numbers.add(opp.scope_number)
        opp.categories = categorize(opp)
        rows.append(opp)

    summary.total_parsed = len(rows)
    if not rows:
        summary.errors.append("All parsed rows were malformed or duplicate")
        logger.error("All parsed rows were malformed or duplicate")
        return summary.finish()

    client = AirtableClient(airtable_key, config.base_id, config.opportunities_table)
    try:
        result, categories_written = _airtable_upsert_with_field_fallback(client, rows)
    except Exception as exc:
        summary.errors.append(f"Airtable upsert failed: {airtable_error_text(exc)}")
        logger.exception("Airtable upsert failed")
        return summary.finish()

    summary.created = int(result.get("created", 0))
    summary.updated = int(result.get("updated", 0))
    summary.categories_written = categories_written
    summary.ok = True
    logger.info(
        "Run complete path=%s total=%s created=%s updated=%s categories_written=%s",
        summary.scrape_path,
        summary.total_parsed,
        summary.created,
        summary.updated,
        summary.categories_written,
    )
    return summary.finish()


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
    scrape = scrape_opportunities(firecrawl_key=os.getenv("FIRECRAWL_API_KEY", ""))
    if not scrape.rows:
        logger.error("Firm matching skipped because scraper returned 0 rows: %s", scrape.errors)
        return 0
    for opp in scrape.rows:
        opp.categories = categorize(opp)

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("SELECT id, capabilities FROM firms")
    firms = cur.fetchall()
    new_matches = 0
    now = datetime.now(timezone.utc).isoformat()

    for firm_id, capabilities in firms:
        cap_tokens = [normalize_text(item).lower() for item in capabilities.split(",") if normalize_text(item)]
        for opp in scrape.rows:
            opportunity_categories = " ".join(opp.categories or []).lower()
            if not opp.scope_number or not any(token in opportunity_categories for token in cap_tokens):
                continue
            try:
                cur.execute(
                    "INSERT INTO matches (firm_id, scope_number, categories, created_at) VALUES (?, ?, ?, ?)",
                    (firm_id, opp.scope_number, ", ".join(opp.categories or ["Other"]), now),
                )
                new_matches += 1
            except sqlite3.IntegrityError:
                logger.debug("Local duplicate match skipped firm_id=%s scope=%s", firm_id, opp.scope_number)

    conn.commit()
    conn.close()
    logger.info("Local matching complete new_matches=%s", new_matches)
    return new_matches


def create_app(db_path: Path = DEFAULT_DB_PATH):
    from flask import Flask, jsonify, redirect, render_template_string, request, url_for

    ensure_db(db_path)
    app = Flask(__name__)

    @app.get("/")
    def home():
        return render_template_string(
            """
            <h1>Be Brown Brave BRIDGE CRM</h1>
            <p>Submit your firm profile for automated Walsh Kokosing opportunity matching.</p>
            <a href='{{ url_for("intake") }}'>Firm Intake Form</a> |
            <a href='{{ url_for("portal") }}'>Firm Portal</a>
            """
        )

    @app.route("/intake", methods=["GET", "POST"])
    def intake():
        if request.method == "POST":
            company = normalize_text(request.form.get("company_name", ""))
            contact = normalize_text(request.form.get("contact_name", ""))
            email = normalize_text(request.form.get("email", "")).lower()
            phone = normalize_text(request.form.get("phone", ""))
            capabilities = normalize_text(request.form.get("capabilities", ""))
            if not (company and contact and email and capabilities):
                return jsonify({"ok": False, "error": "company_name, contact_name, email, and capabilities are required"}), 400
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO firms (company_name, contact_name, email, phone, capabilities, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (company, contact, email, phone, capabilities, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
            new_matches = match_firms_to_opportunities(db_path)
            logger.info("Firm intake saved email=%s new_matches=%s", email, new_matches)
            return redirect(url_for("portal", email=email))

        categories = sorted(CATEGORY_KEYWORDS.keys())
        return render_template_string(
            """
            <h2>Firm Intake</h2>
            <p>Use comma-separated capabilities. Examples: {{ categories|join(', ') }}</p>
            <form method="post">
              <label>Company Name <input name="company_name" required></label><br/>
              <label>Contact Name <input name="contact_name" required></label><br/>
              <label>Email <input name="email" type="email" required></label><br/>
              <label>Phone <input name="phone"></label><br/>
              <label>Capabilities<br/><textarea name="capabilities" required></textarea></label><br/>
              <button type="submit">Submit</button>
            </form>
            """,
            categories=categories,
        )

    @app.get("/portal")
    def portal():
        email = normalize_text(request.args.get("email", "")).lower()
        if not email:
            return jsonify({"ok": False, "error": "Provide ?email=you@example.com"}), 400
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT id, company_name, capabilities FROM firms WHERE email = ? ORDER BY id DESC LIMIT 1", (email,))
        firm = cur.fetchone()
        if not firm:
            conn.close()
            return jsonify({"ok": False, "error": "Firm not found"}), 404
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
            {% else %}
              <li>No matches yet. We will keep checking as opportunities update.</li>
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
            return jsonify({"ok": False, "error": "Invalid decision"}), 400
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE matches SET status = ? WHERE id = ?", (decision, match_id))
        conn.commit()
        conn.close()
        logger.info("Match decision recorded match_id=%s decision=%s", match_id, decision)
        return jsonify({"ok": True, "match_id": match_id, "decision": decision})

    @app.get("/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "service": "bbb-bridge-crm",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "scheduler": {"timezone": SCHEDULER_TIMEZONE, "hour": SCHEDULER_HOUR, "minute": SCHEDULER_MINUTE},
            }
        )

    @app.post("/run")
    def trigger():
        scrape_result = run_once()
        match_result = match_firms_to_opportunities(db_path) if scrape_result.get("ok") else 0
        status = 200 if scrape_result.get("ok") else 500
        return jsonify({"ok": bool(scrape_result.get("ok")), "scrape": scrape_result, "new_matches": match_result}), status

    return app


def serve_dashboard(host: str = "0.0.0.0", port: int = 8787) -> None:
    create_app().run(host=host, port=port)


def run_scheduler() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from zoneinfo import ZoneInfo

    scheduler = BlockingScheduler(timezone=ZoneInfo(SCHEDULER_TIMEZONE))
    scheduler.add_job(run_once, "cron", hour=SCHEDULER_HOUR, minute=SCHEDULER_MINUTE, id="walsh_kokosing_daily")
    logger.info("Scheduler started: daily at %02d:%02d %s", SCHEDULER_HOUR, SCHEDULER_MINUTE, SCHEDULER_TIMEZONE)
    scheduler.start()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Be Brown Brave BRIDGE CRM backend")
    parser.add_argument("--once", action="store_true", help="Run one opportunity sync immediately")
    parser.add_argument("--schedule", action="store_true", help="Run 2:00 AM ET daily scheduler")
    parser.add_argument("--serve", action="store_true", help="Start customer-facing web app")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="Web server host")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8787")), help="Web server port")
    args = parser.parse_args()

    if args.once:
        print(json.dumps(run_once(), indent=2))
    if args.schedule:
        run_scheduler()
    if args.serve:
        serve_dashboard(host=args.host, port=args.port)
    if not (args.once or args.schedule or args.serve):
        parser.print_help()


if __name__ == "__main__":
    main()
