"""
Local live dashboard: log, edit, and delete trades from the browser instead
of the Excel journal. Everything is stored in trades.db (SQLite) next to
this script.

First time setup:
    python migrate_from_xlsx.py     (imports your existing journal once)
    python server.py                (starts the server)

Then open http://127.0.0.1:5151 in a browser.

Password protection (only matters if you deploy this somewhere reachable
off your own machine -- running locally with no DASHBOARD_PASSWORD set
skips the login screen entirely, exactly like before):
    Set the DASHBOARD_PASSWORD environment variable before starting the
    server, and a login screen gates every page and API route. Also set
    SECRET_KEY (any random string) so login sessions survive a restart --
    without it, everyone gets logged out each time the server restarts.

Local secrets, without retyping them every session:
    Create a file named .env next to this script (already gitignored -- it
    will never get committed) with one KEY=VALUE per line, e.g.:
        DASHBOARD_PASSWORD=whatever-you-want
        SECRET_KEY=any-random-string
        FRED_API_KEY=your-fred-key
        ANTHROPIC_API_KEY=your-anthropic-key
    Then just run `python server.py` -- no PowerShell $env: commands needed.
    A real environment variable set in the shell always wins over .env, so
    this is purely a local convenience, not a replacement for how Render
    (or any real deployment) is configured.
"""
import os
import secrets
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for

import ai_bias
import backup
import db
import debt_model
import fred_calendar
import fred_sync
import fundamentals
import market_data
import rate_calendar

HERE = Path(__file__).parent


def _load_dotenv():
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# Module level, not inside `if __name__ == "__main__"` -- gunicorn imports
# this file directly on Render and never runs that block, so a backup
# trigger placed there would only ever fire during local dev.
backup.backup_if_needed()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Felix Trade Journal — Login</title>
<style>
  html,body{{margin:0;height:100%;background:#0d0d0d;color:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    display:flex;align-items:center;justify-content:center;}}
  form{{background:#1a1a19;border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:32px 28px;width:280px;}}
  h1{{font-size:16px;margin:0 0 18px;}}
  input{{width:100%;background:#212120;border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#fff;
    padding:9px 10px;font-size:14px;box-sizing:border-box;margin-bottom:12px;}}
  button{{width:100%;background:#3987e5;border:none;border-radius:8px;color:#fff;font-weight:650;padding:10px;
    font-size:13.5px;cursor:pointer;}}
  .error{{color:#e66767;font-size:12.5px;margin-bottom:10px;}}
</style></head><body>
<form method="post">
  <h1>Felix Trade Journal</h1>
  {error}
  <input type="password" name="password" placeholder="Password" autofocus>
  <button type="submit">Enter</button>
</form>
</body></html>"""


@app.before_request
def require_login():
    if not DASHBOARD_PASSWORD:
        return  # no password configured -- e.g. plain local use, skip the gate entirely
    if request.endpoint == "login":
        return
    if not session.get("authed"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["authed"] = True
            return redirect(url_for("index"))
        error = '<div class="error">Incorrect password.</div>'
    return LOGIN_PAGE.format(error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# Only these shared assets are servable as static files -- deliberately not the
# whole folder, since that also holds trades.db and the source scripts.
STATIC_ASSETS = {"dashboard-core.js", "dashboard-core.css"}

REQUIRED_FIELDS = ["date"]
ALLOWED_FIELDS = [
    "date", "session", "pair", "direction", "risk", "rr", "pnl",
    "notes", "chart_daily", "chart_4h", "chart_30m", "strategy_id",
]


def clean_payload(body):
    fields = {k: body.get(k) for k in ALLOWED_FIELDS if k in body}
    for missing in REQUIRED_FIELDS:
        if not fields.get(missing):
            raise ValueError(f"'{missing}' is required")
    for k in ("pair",):
        if fields.get(k):
            fields[k] = str(fields[k]).strip().upper()
    return fields


@app.get("/")
def index():
    return send_from_directory(HERE, "live_dashboard.html")


@app.get("/<path:filename>")
def static_asset(filename):
    if filename not in STATIC_ASSETS:
        return jsonify({"error": "not found"}), 404
    return send_from_directory(HERE, filename)


@app.get("/api/trades")
def api_list():
    return jsonify(db.list_trades())


@app.post("/api/trades")
def api_create():
    try:
        fields = clean_payload(request.get_json(force=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    new_id = db.insert_trade(fields)
    return jsonify({"id": new_id}), 201


@app.put("/api/trades/<int:trade_id>")
def api_update(trade_id):
    try:
        fields = clean_payload(request.get_json(force=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    db.update_trade(trade_id, fields)
    return jsonify({"ok": True})


@app.delete("/api/trades/<int:trade_id>")
def api_delete(trade_id):
    db.delete_trade(trade_id)
    return jsonify({"ok": True})


@app.get("/api/settings/<key>")
def api_get_setting(key):
    return jsonify({"key": key, "value": db.get_setting(key)})


@app.put("/api/settings/<key>")
def api_put_setting(key):
    body = request.get_json(force=True)
    db.set_setting(key, str(body.get("value", "")))
    return jsonify({"ok": True})


@app.get("/api/strategies")
def api_list_strategies():
    return jsonify(db.list_strategies())


@app.post("/api/strategies")
def api_create_strategy():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    new_id = db.create_strategy(name, body.get("description") or "")
    return jsonify({"id": new_id}), 201


@app.delete("/api/strategies/<int:strategy_id>")
def api_delete_strategy(strategy_id):
    db.delete_strategy(strategy_id)
    return jsonify({"ok": True})


@app.get("/api/macro")
def api_get_macro():
    return jsonify(db.list_macro())


@app.put("/api/macro/<currency>")
def api_put_macro(currency):
    currency = currency.upper()
    if currency not in db.MAJOR_CURRENCIES:
        return jsonify({"error": "unknown currency"}), 400
    body = request.get_json(force=True)
    db.upsert_macro(currency, body)
    return jsonify({"ok": True})


@app.post("/api/macro/sync")
def api_macro_sync():
    try:
        report = fred_sync.sync()
    except Exception as e:
        return jsonify({"error": f"FRED sync failed: {e}"}), 502
    return jsonify(report)


@app.get("/api/prices")
def api_prices():
    return jsonify(market_data.get_quotes())


@app.get("/api/news/<currency>")
def api_currency_news(currency):
    currency = currency.upper()
    if currency not in db.MAJOR_CURRENCIES:
        return jsonify({"error": "unknown currency"}), 400
    return jsonify(market_data.get_currency_news(currency))


@app.get("/api/news-summary")
def api_news_summary():
    url = request.args.get("url", "")
    return jsonify({"summary": market_data.get_article_summary(url)})


@app.get("/api/backups")
def api_list_backups():
    return jsonify(backup.list_backups())


@app.post("/api/backups")
def api_backup_now():
    path = backup.backup_now()
    return jsonify({"ok": path is not None, "filename": path.name if path else None})


@app.get("/api/debt-snapshot")
def api_debt_snapshot():
    return jsonify(debt_model.snapshot())


@app.get("/api/release-calendar")
def api_release_calendar():
    return jsonify(fred_calendar.next_release_dates())


@app.get("/api/rate-calendar")
def api_rate_calendar():
    return jsonify(rate_calendar.next_decisions())


@app.get("/api/fundamentals/<pair>")
def api_fundamentals(pair):
    comparison = fundamentals.compare_pair(pair, db.list_macro())
    if comparison is None:
        return jsonify({"error": "Expected a 6-letter pair like EURUSD"}), 400
    return jsonify(comparison)


@app.post("/api/analyze")
def api_analyze():
    body = request.get_json(force=True)
    pair = (body.get("pair") or "").upper().strip()
    chart_link = body.get("chart_link") or None
    if not pair:
        return jsonify({"error": "pair is required"}), 400

    comparison = fundamentals.compare_pair(pair, db.list_macro())
    strategy_text = "\n\n".join(f"### {s['name']}\n{s['description'] or ''}" for s in db.list_strategies())
    try:
        analysis = ai_bias.run_bias_check(pair, strategy_text, comparison, chart_link)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"AI call failed: {e}"}), 502
    return jsonify({"analysis": analysis})


if __name__ == "__main__":
    if not db.DB_PATH.exists():
        print("No trades.db found yet -- run 'python migrate_from_xlsx.py' first to import your journal.")
    if DASHBOARD_PASSWORD:
        print("Password protection is ON.")
    else:
        print("Password protection is OFF (no DASHBOARD_PASSWORD set) -- fine for local use, required before hosting this anywhere reachable by others.")
    # Local runs stay on 127.0.0.1 (loopback only) unless HOST is set explicitly.
    # A real deployment (Render, etc.) runs this via gunicorn instead, not this block.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5151))
    app.run(host=host, port=port, debug=False)
