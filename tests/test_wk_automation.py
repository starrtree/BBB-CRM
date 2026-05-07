from pathlib import Path

import pytest

from src.wk_automation import (
    Opportunity,
    build_airtable_fields,
    categorize,
    ensure_db,
    parse_html_table,
    parse_markdown_table,
)


def test_categorize_multi_match():
    opp = Opportunity(scope_number="123", scope_description="Concrete paving and drainage", phase="Phase 1")
    cats = categorize(opp)
    assert "Concrete" in cats
    assert "Paving" in cats
    assert "Utilities" in cats


def test_parse_markdown_table():
    md = """
| Scope Number | Phase | Scope Description | Price Range | Scope Status | Release for Bid | Quotes Due |
| --- | --- | --- | --- | --- | --- | --- |
| 11-22 | A | Electrical conduit install | $10k-$20k | Open | 2026-04-01 | 2026-04-10 |
"""
    rows = parse_markdown_table(md)
    assert len(rows) == 1
    assert rows[0].scope_number == "11-22"
    assert rows[0].phase == "A"


def test_parse_html_table():
    html = """
<table>
  <tr><th>Scope Number</th><th>Phase</th><th>Scope Description</th><th>Price Range</th><th>Scope Status</th><th>Release for Bid</th><th>Quotes Due</th></tr>
  <tr><td>1</td><td>B</td><td>Sitework grading</td><td>$5k</td><td>Open</td><td>Apr 1</td><td>Apr 9</td></tr>
</table>
"""
    rows = parse_html_table(html)
    assert len(rows) == 1
    assert rows[0].scope_description == "Sitework grading"


def test_build_airtable_fields_without_categories():
    opp = Opportunity(scope_number="10", scope_description="Utility work")
    fields = build_airtable_fields(opp, include_categories=False)
    assert "Categories" not in fields
    assert fields["Scope Number"] == "10"
    assert "Bid Title" in fields


def test_ensure_db_creates_tables(tmp_path: Path):
    db_path = tmp_path / "app.db"
    ensure_db(db_path)
    assert db_path.exists()


def test_markdown_parser_skips_malformed_rows_without_scope_number():
    md = """
| Scope Number | Phase | Scope Description | Price Range | Scope Status | Release for Bid | Quotes Due |
| --- | --- | --- | --- | --- | --- | --- |
|  | A | Missing scope | $10k | Open | TBD | Deferred |
| 42 | B | Asphalt paving | $20k | Open | TBD | Deferred |
"""
    rows = parse_markdown_table(md)
    assert len(rows) == 1
    assert rows[0].scope_number == "42"


def test_build_airtable_fields_skips_unparseable_dates():
    opp = Opportunity(scope_number="99", scope_description="General construction", release_for_bid="TBD", quotes_due="Deferred")
    fields = build_airtable_fields(opp)
    assert "Release for Bid" not in fields
    assert "Deadline/Quotes Due" not in fields


def test_markdown_parser_handles_spaced_deadline_header():
    md = """
| Scope Number | Deadline / Quotes Due | Scope Description |
| --- | --- | --- |
| 77 | 2026-06-01 | Drainage utilities |
"""
    rows = parse_markdown_table(md)
    assert len(rows) == 1
    assert rows[0].quotes_due == "2026-06-01"


def test_admin_dashboard_route_if_flask_installed(tmp_path: Path):
    pytest.importorskip("flask")
    from src.wk_automation import create_app

    app = create_app(tmp_path / "admin.db")
    client = app.test_client()

    response = client.get("/admin")
    assert response.status_code == 200
    assert b"Be Brown Brave BRIDGE CRM Admin" in response.data
    assert b"Run Opportunity Scraper Now" in response.data
    assert b"Run Matching Now" in response.data


def test_health_endpoint_does_not_expose_secrets(tmp_path: Path, monkeypatch):
    pytest.importorskip("flask")
    from src.wk_automation import create_app

    monkeypatch.setenv("AIRTABLE_API_KEY", "pat_secret_should_not_render")
    app = create_app(tmp_path / "health.db")
    client = app.test_client()

    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["airtable"]["api_key_configured"] is True
    assert "pat_secret_should_not_render" not in response.get_data(as_text=True)
