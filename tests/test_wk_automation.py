from src.wk_automation import (
    Opportunity,
    build_airtable_fields,
    categorize,
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
