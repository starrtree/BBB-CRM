from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from flask import jsonify, redirect, render_template, request

from .wk_automation import AIRTABLE_API_ROOT, DEFAULT_DB_PATH, AirtableConfig, create_app as create_backend_app, health_payload

FIRMS_CACHE: Dict[str, Any] = {"expires_at": 0.0, "payload": None}
OPPORTUNITIES_CACHE: Dict[str, Any] = {"expires_at": 0.0, "payload": None}
CACHE_SECONDS = int(os.getenv("CRM_CACHE_SECONDS", os.getenv("FIRMS_CACHE_SECONDS", "300")))


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _field(fields: Dict[str, Any], *names: str, default: Any = "") -> Any:
    normalized = {_norm_key(key): value for key, value in fields.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if value not in (None, "", []):
            return value
    return default


def _as_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,;/|]+", str(value)) if item.strip()]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"yes", "true", "y", "1", "ready", "matched", "active"}


def _initials(name: str) -> str:
    parts = [part[0] for part in re.findall(r"[A-Za-z0-9]+", name)[:2]]
    return "".join(parts).upper() or "BB"


def _firm_status(fields: Dict[str, Any], ready: Any, support: Any, matched: Any) -> str:
    raw = str(_field(fields, "Status", "Match Status", "CRM Status", default="")).lower()
    if any(word in raw for word in ("support", "help", "follow")):
        return "support"
    if any(word in raw for word in ("matched", "active", "won")):
        return "matched"
    if any(word in raw for word in ("lost", "denied", "rejected", "no match")):
        return "lost"
    if _truthy(support):
        return "support"
    if _truthy(matched):
        return "matched"
    if str(ready).strip().lower() in {"no", "false", "not ready"}:
        return "support"
    return "pending"


def _normalize_firm(record: Dict[str, Any]) -> Dict[str, Any]:
    fields = record.get("fields", {})
    name = str(_field(fields, "Business Name", "Firm Name", "Company Name", "Company", "Legal Business Name", "Name", default="Unnamed Firm"))
    contact = str(_field(fields, "Contact Name", "Primary Contact", "Owner Name", "Business Owner", "Full Name", "Contact", default=""))
    trade = str(_field(fields, "Industry / Trade", "Industry", "Trade", "Business Field", "Service Category", "Category", "Specialty", default="Other"))
    capabilities = str(_field(fields, "Capabilities / Services", "Capabilities", "Services", "Business Description", "Description", "Business/Specialty", default="No capabilities listed yet."))
    certifications = _as_list(_field(fields, "Certifications", "Certification", "Certs", default=[]))
    ready = _field(fields, "Ready to Bid?", "Ready to Bid", "Bid Ready", default="Unknown")
    support = _field(fields, "Needs Support?", "Needs Support", "Support Needed", default="Unknown")
    match = _field(fields, "Matched Opportunity", "Matched Opportunities", "Firm Matches", "Matched?", default="")
    status = _firm_status(fields, ready, support, match)

    return {
        "id": record.get("id", ""),
        "firmId": _field(fields, "Firm ID", "ID", "Business ID", default=record.get("id", "")),
        "name": name,
        "firm_name": name,
        "contact": contact or "No contact listed",
        "contact_name": contact or "No contact listed",
        "email": _field(fields, "Email", "Contact Email", "Business Email", default=""),
        "phone": _field(fields, "Phone", "Phone Number", "Contact Phone", default=""),
        "website": _field(fields, "Website", "Business Website", "URL", default=""),
        "address": _field(fields, "Business Address", "Address", "Location", default=""),
        "trade": trade,
        "status": status,
        "logo": _initials(name),
        "certs": certifications,
        "certifications": certifications,
        "priority": "WBE" in certifications and "DBE" in certifications,
        "ready": ready,
        "ready_to_bid": ready,
        "support": support,
        "support_needed": support,
        "cap": capabilities,
        "capabilities": capabilities,
        "match": str(match) if match else "No active matched opportunity yet",
        "matched_opportunity": str(match) if match else "No active matched opportunity yet",
        "reason": "Live Airtable firm record. Match reasoning will populate after the matching engine is connected.",
        "notes": _field(fields, "Notes", "Internal Notes", default=""),
    }


def _normalize_opportunity(record: Dict[str, Any]) -> Dict[str, Any]:
    fields = record.get("fields", {})
    scope_number = str(_field(fields, "Scope Number", "Scope #", "Scope", default=""))
    description = str(_field(fields, "Scope Description", "Description", default=""))
    title = str(_field(fields, "Bid Title", "Title", default=description or scope_number or "Untitled Opportunity"))
    firm_matches = _field(fields, "Firm Matches", "Matched Firms", default=[])
    match_ids = firm_matches if isinstance(firm_matches, list) else _as_list(firm_matches)
    return {
        "id": record.get("id", ""),
        "scope_number": scope_number,
        "title": title,
        "phase": _field(fields, "Phase", default=""),
        "description": description,
        "price_range": _field(fields, "Price Range", "Budget", default=""),
        "status": _field(fields, "Scope Status", "Status", default=""),
        "release_for_bid": _field(fields, "Release for Bid", "Release Date", default=""),
        "deadline": _field(fields, "Deadline/Quotes Due", "Quotes Due", "Deadline", "Due Date", default=""),
        "categories": _as_list(_field(fields, "Categories", "Category", default=[])),
        "source_url": _field(fields, "Source URL", "URL", default=""),
        "last_scraped": _field(fields, "Last Scraped", "Updated", default=""),
        "match_count": len(match_ids),
        "firm_match_ids": match_ids,
    }


def _fetch_airtable_records(table: str) -> List[Dict[str, Any]]:
    import requests
    api_key = os.getenv("AIRTABLE_API_KEY", "")
    config = AirtableConfig.from_env()
    if not api_key:
        raise RuntimeError("AIRTABLE_API_KEY is not configured")
    if not table:
        raise RuntimeError("The requested Airtable table is not configured")
    url = f"{AIRTABLE_API_ROOT}/{config.base_id}/{quote(table, safe='')}"
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {api_key}"})
    params: Dict[str, Any] = {"pageSize": 100}
    records: List[Dict[str, Any]] = []
    while True:
        response = session.get(url, params=params, timeout=30)
        response.raise_for_status()
        body = response.json()
        records.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            return records
        params["offset"] = offset


def _firms_payload(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force_refresh and FIRMS_CACHE["payload"] and now < float(FIRMS_CACHE["expires_at"]):
        return FIRMS_CACHE["payload"]
    config = AirtableConfig.from_env()
    firms = [_normalize_firm(record) for record in _fetch_airtable_records(config.firms_table)]
    payload = {"ok": True, "source": "airtable", "count": len(firms), "firms": firms}
    FIRMS_CACHE.update(payload=payload, expires_at=now + CACHE_SECONDS)
    return payload


def _opportunities_payload(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force_refresh and OPPORTUNITIES_CACHE["payload"] and now < float(OPPORTUNITIES_CACHE["expires_at"]):
        return OPPORTUNITIES_CACHE["payload"]
    config = AirtableConfig.from_env()
    opportunities = [_normalize_opportunity(record) for record in _fetch_airtable_records(config.opportunities_table)]
    opportunities.sort(key=lambda item: (str(item.get("deadline", "")), str(item.get("scope_number", ""))))
    payload = {"ok": True, "source": "airtable", "count": len(opportunities), "opportunities": opportunities}
    OPPORTUNITIES_CACHE.update(payload=payload, expires_at=now + CACHE_SECONDS)
    return payload


def create_app(db_path: Path = DEFAULT_DB_PATH):
    """Create the backend app plus the mother-facing CRM UI."""
    app = create_backend_app(db_path)

    @app.before_request
    def route_root_to_crm():
        if request.path == "/":
            return redirect("/crm")
        return None

    @app.after_request
    def add_crm_link_to_admin(response):
        if request.path != "/admin" or not response.content_type.startswith("text/html"):
            return response
        html = response.get_data(as_text=True)
        html = html.replace(
            '<a class="nav-item active" href="/admin">Admin Dashboard</a>',
            '<a class="nav-item active" href="/admin">Admin Dashboard</a>\n                  <a class="nav-item" href="/crm">Michelle CRM View</a>',
        )
        html = html.replace(
            "<p>Monitor automation health, review sync outcomes, and run opportunity updates manually when needed.</p>",
            "<p>Monitor automation health, review sync outcomes, and run opportunity updates manually when needed.</p>\n<p style=\"margin-top:.8rem;\"><a href=\"/crm\" style=\"display:inline-block;background:linear-gradient(180deg,#8a5a3a,#5f3a24);border:2px solid #5ea1e8;color:#fff;text-decoration:none;padding:.7rem .95rem;border-radius:10px;font-weight:800;\">Open Michelle CRM View</a></p>",
        )
        response.set_data(html)
        return response

    @app.get("/crm")
    def crm_dashboard():
        return render_template("crm.html", health=health_payload())

    @app.get("/admin/heart")
    def legacy_living_heart_dashboard():
        return redirect("/crm")

    @app.get("/api/firms")
    def firms_api():
        try:
            return jsonify(_firms_payload(request.args.get("refresh") in {"1", "true", "yes"}))
        except Exception as exc:
            return jsonify({"ok": False, "source": "airtable", "error": str(exc), "firms": []}), 500

    @app.get("/api/opportunities")
    def opportunities_api():
        try:
            return jsonify(_opportunities_payload(request.args.get("refresh") in {"1", "true", "yes"}))
        except Exception as exc:
            return jsonify({"ok": False, "source": "airtable", "error": str(exc), "opportunities": []}), 500

    return app
