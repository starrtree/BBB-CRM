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
FIRMS_CACHE_SECONDS = int(os.getenv("FIRMS_CACHE_SECONDS", "300"))


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


def _status(fields: Dict[str, Any], ready: Any, support: Any, matched: Any) -> str:
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
    certs = _as_list(_field(fields, "Certifications", "Certification", "Certs", default=[]))
    ready = _field(fields, "Ready to Bid?", "Ready to Bid", "Bid Ready", default="Unknown")
    support = _field(fields, "Needs Support?", "Needs Support", "Support Needed", default="Unknown")
    match = _field(fields, "Matched Opportunity", "Matched Opportunities", "Firm Matches", "Matched?", default="")
    status = _status(fields, ready, support, match)
    priority = "WBE" in certs and "DBE" in certs

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
        "logo_text": _initials(name),
        "certs": certs,
        "certifications": certs,
        "priority": priority,
        "ready": ready,
        "ready_to_bid": ready,
        "support": support,
        "support_needed": support,
        "cap": capabilities,
        "capabilities": capabilities,
        "match": str(match) if match else "No active matched opportunity yet",
        "matched_opportunity": str(match) if match else "No active matched opportunity yet",
        "reason": "Live Airtable firm record. Match reasoning will populate after the matching engine is connected.",
        "match_reason": "Live Airtable firm record. Match reasoning will populate after the matching engine is connected.",
        "notes": _field(fields, "Notes", "Internal Notes", default=""),
    }


def _airtable_firms_payload(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force_refresh and FIRMS_CACHE["payload"] and now < float(FIRMS_CACHE["expires_at"]):
        return FIRMS_CACHE["payload"]

    import requests

    api_key = os.getenv("AIRTABLE_API_KEY", "")
    config = AirtableConfig.from_env()
    if not api_key:
        return {"ok": False, "source": "airtable", "error": "AIRTABLE_API_KEY is not configured", "firms": []}
    if not config.firms_table:
        return {"ok": False, "source": "airtable", "error": "AIRTABLE_FIRMS_TABLE_ID is not configured", "firms": []}

    url = f"{AIRTABLE_API_ROOT}/{config.base_id}/{quote(config.firms_table, safe='')}"
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
            break
        params["offset"] = offset

    firms = [_normalize_firm(record) for record in records]
    payload = {"ok": True, "source": "airtable", "count": len(firms), "firms": firms}
    FIRMS_CACHE["payload"] = payload
    FIRMS_CACHE["expires_at"] = now + FIRMS_CACHE_SECONDS
    return payload


def create_app(db_path: Path = DEFAULT_DB_PATH):
    """Create the backend app plus the mother-facing CRM UI."""
    app = create_backend_app(db_path)

    @app.after_request
    def inject_crm_link(response):
        """Add CRM navigation and live Airtable fetching to existing templates."""
        if not response.content_type.startswith("text/html"):
            return response

        html = response.get_data(as_text=True)
        if request.path == "/admin":
            html = html.replace(
                '<a class="nav-item active" href="/admin">Admin Dashboard</a>',
                '<a class="nav-item active" href="/admin">Admin Dashboard</a>\n'
                '                  <a class="nav-item" href="/crm">Michelle CRM View</a>',
            )
            html = html.replace(
                "<p>Monitor automation health, review sync outcomes, and run opportunity updates manually when needed.</p>",
                "<p>Monitor automation health, review sync outcomes, and run opportunity updates manually when needed.</p>\n"
                "                    <p style=\"margin-top: .8rem;\"><a href=\"/crm\" style=\"display:inline-block;background:linear-gradient(180deg,#8a5a3a,#5f3a24);border:2px solid #5ea1e8;color:#fff;text-decoration:none;padding:.7rem .95rem;border-radius:10px;font-weight:800;\">Open Michelle CRM View</a></p>",
            )
        elif request.path == "/crm":
            html = html.replace("const firms=", "let firms=", 1)
            html = html.replace(
                "renderStats();renderFilters();renderAll();updateTransform();",
                "async function loadLiveFirms(){try{const response=await fetch('/api/firms');const payload=await response.json();if(payload.ok&&Array.isArray(payload.firms)&&payload.firms.length){firms=payload.firms;renderStats();renderAll();}}catch(error){console.warn('Using demo firm data because live Airtable firms could not load',error);}}renderStats();renderFilters();renderAll();updateTransform();loadLiveFirms();",
                1,
            )
        response.set_data(html)
        return response

    @app.get("/crm")
    def crm_dashboard():
        return render_template("living_heart.html", health=health_payload())

    @app.get("/admin/heart")
    def living_heart_dashboard():
        return redirect("/crm")

    @app.get("/api/firms")
    def firms_api():
        force_refresh = request.args.get("refresh") in {"1", "true", "yes"}
        try:
            payload = _airtable_firms_payload(force_refresh=force_refresh)
            return jsonify(payload), 200 if payload.get("ok") else 500
        except Exception as exc:
            return jsonify({"ok": False, "source": "airtable", "error": str(exc), "firms": []}), 500

    @app.get("/api/firms/mock")
    def mock_firms_api():
        return jsonify({"ok": True, "source": "mock", "message": "The CRM UI now tries /api/firms first and falls back to demo data if Airtable is unavailable."})

    return app
