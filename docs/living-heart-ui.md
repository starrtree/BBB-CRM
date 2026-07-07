# Living Heart Network UI

The repo now includes an experimental mother-facing CRM dashboard at:

```text
/admin/heart
```

This interface is meant to make Be Brown Brave client management feel like a living community map instead of a spreadsheet. It uses a chocolate-brown and cream brand palette, an interactive heart made of client blocks, status colors, hover tooltips, zooming, panning, and a detail drawer.

## Current implementation

Files added:

```text
src/ui_app.py
src/templates/living_heart.html
```

`src/ui_app.py` wraps the existing backend Flask app and adds the Living Heart route. The template currently uses safe local demo data in the browser so the UI can be reviewed before Airtable Firms endpoints are finished.

## Run locally

Use the UI wrapper app instead of the backend-only app:

```bash
gunicorn 'src.ui_app:create_app()' --bind 0.0.0.0:$PORT
```

For quick local development you can also run Flask against `src.ui_app:create_app` depending on your environment.

## Design behavior

- Each heart block represents one firm/client.
- Green means matched.
- Orange means pending/in review.
- Blue means support needed.
- Red means no current match/lost.
- Gold outline means priority firm.
- Scroll zooms the heart.
- Drag pans the heart.
- Hover reveals a mini profile tooltip.
- Click opens the detailed client drawer.

## Future backend connection

Replace the local demo data in `src/templates/living_heart.html` with data from a future real endpoint such as:

```text
/api/firms
```

That endpoint should read from the Airtable Firms table, normalize firm fields, and return status/match data for the dashboard.

## Security warning

This UI is not authenticated yet. Do not expose `/admin/heart` publicly until authentication, IP allowlisting, VPN access, or another admin-only protection layer is added.
