from __future__ import annotations

from pathlib import Path

from flask import jsonify, redirect, render_template, request

from .wk_automation import DEFAULT_DB_PATH, create_app as create_backend_app, health_payload


def create_app(db_path: Path = DEFAULT_DB_PATH):
    """Create the backend app plus the mother-facing CRM UI."""
    app = create_backend_app(db_path)

    @app.after_request
    def inject_crm_link(response):
        """Add the mother-facing CRM link into the existing backend admin page."""
        if request.path != "/admin" or not response.content_type.startswith("text/html"):
            return response

        html = response.get_data(as_text=True)
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
        response.set_data(html)
        return response

    @app.get("/crm")
    def crm_dashboard():
        return render_template("living_heart.html", health=health_payload())

    @app.get("/admin/heart")
    def living_heart_dashboard():
        return redirect("/crm")

    @app.get("/api/firms/mock")
    def mock_firms_api():
        return jsonify({"ok": True, "source": "mock", "message": "The CRM UI currently uses local demo data until Airtable Firms endpoints are added."})

    return app
