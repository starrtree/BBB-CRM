from __future__ import annotations

from pathlib import Path

from flask import jsonify, render_template

from .wk_automation import DEFAULT_DB_PATH, create_app as create_backend_app, health_payload


def create_app(db_path: Path = DEFAULT_DB_PATH):
    """Create the backend app plus the experimental Living Heart CRM UI."""
    app = create_backend_app(db_path)

    @app.get("/admin/heart")
    def living_heart_dashboard():
        return render_template("living_heart.html", health=health_payload())

    @app.get("/api/firms/mock")
    def mock_firms_api():
        return jsonify({"ok": True, "source": "mock", "message": "The Living Heart UI currently uses local demo data until Airtable Firms endpoints are added."})

    return app
