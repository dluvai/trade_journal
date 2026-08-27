"""
Live dashboard: log, edit, and delete trades from the browser instead of
the Excel journal. Everything is stored in a shared Turso (libSQL) database
-- see TURSO_DATABASE_URL/TURSO_AUTH_TOKEN below -- not a local file, so
local dev and any deployment see the same data.

First time setup:
    python create_user.py <username>   (creates your login)
    python server.py                   (starts the server)

Then open http://127.0.0.1:5151 in a browser.

Login: each person gets their own username/password (see create_user.py).
A brand-new database with zero users skips the login screen entirely; the
moment one account exists, every page and API route requires a login.
Set SECRET_KEY (any random string) so sessions survive a restart -- without
it, everyone gets logged out each time the server restarts.

Local secrets, without retyping them every session:
    Create a file named .env next to this script (already gitignored -- it
    will never get committed) with one KEY=VALUE per line, e.g.:
        TURSO_DATABASE_URL=libsql://your-db.turso.io
        TURSO_AUTH_TOKEN=your-turso-token
        SECRET_KEY=any-random-string
        FRED_API_KEY=your-fred-key
        ANTHROPIC_API_KEY=your-anthropic-key
        RESEND_API_KEY=your-resend-key
        RESEND_FROM_EMAIL=onboarding@resend.dev
    Then just run `python server.py` -- no PowerShell $env: commands needed.
    A real environment variable set in the shell always wins over .env, so
    this is purely a local convenience, not a replacement for how Render
    (or any real deployment) is configured.
"""
import io
import json
import os
import re
import secrets
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from PIL import Image
from werkzeug.security import check_password_hash, generate_password_hash

import ai_bias
import auth
import auth_pages
import auto_sync
import backup
import calendar_view
import countries
import db
import debt_model
import email_sender
import fred_calendar
import fundamentals
import import_trades
import macro_sync
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
auto_sync.start()

app = Flask(__name__)
# Templates are cached by default once debug=False -- Render restarts the
# whole process on every deploy anyway (so it'd never matter there), but
# without this a local dev edit to a .html template needs a server restart
# to show up, same as a .py change would.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

PUBLIC_ENDPOINTS = {
    "login", "signup", "verify_email", "resend_verification_code",
    "verify_email_resume", "forgot_password", "reset_password",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+\-\s()]{6,20}$")


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if db.count_users() == 0:
        return  # fresh checkout, nobody provisioned yet -- skip the gate entirely
    if not session.get("user_id"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        user = db.get_user_by_username((request.form.get("username") or "").strip())
        password = request.form.get("password") or ""
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        error = '<div class="error">Incorrect username or password.</div>'
    return auth_pages.render("Login", auth_pages.LOGIN_PAGE.format(error=error))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _mask_email(email):
    name, _, domain = email.partition("@")
    visible = name[:2] if len(name) > 2 else name[:1]
    return f"{visible}{'*' * max(1, len(name) - len(visible))}@{domain}"


REQUIRED_SIGNUP_FIELDS = ["first_name", "last_name", "username", "email"]
OPTIONAL_SIGNUP_FIELDS = ["country", "address_line1", "address_city", "address_postal_code", "phone"]


def _validate_profile_fields(fields):
    # Shared by signup and the profile page's "edit account" form -- both
    # only ever set these when non-empty, so empty stays valid (optional).
    if fields.get("country") and fields["country"] not in countries.COUNTRY_CODES:
        return "Unknown country."
    if fields.get("phone") and not PHONE_RE.match(fields["phone"]):
        return "Enter a valid phone number."
    return None


@app.route("/signup", methods=["GET", "POST"])
def signup():
    fields = {k: "" for k in REQUIRED_SIGNUP_FIELDS + OPTIONAL_SIGNUP_FIELDS}
    error = ""
    is_json = request.method == "POST" and request.is_json
    if request.method == "POST":
        source = request.get_json(silent=True) or {} if is_json else request.form
        fields = {k: (source.get(k) or "").strip() for k in fields}
        password = source.get("password") or ""
        confirm_password = source.get("confirm_password") or ""

        if not all(fields[k] for k in REQUIRED_SIGNUP_FIELDS) or not password or not confirm_password:
            error = "First name, last name, username, email, and password are required."
        elif not EMAIL_RE.match(fields["email"]):
            error = "Enter a valid email address."
        elif password != confirm_password:
            error = "Passwords don't match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif db.get_user_by_username(fields["username"]):
            error = "That username is already taken."
        elif db.get_user_by_email(fields["email"]):
            error = "That email is already registered."
        else:
            error = _validate_profile_fields(fields)

        if not error:
            user_id = db.create_user(
                fields["username"], generate_password_hash(password),
                fields["first_name"], fields["last_name"], fields["email"],
                fields["country"] or None, fields["address_line1"] or None,
                fields["address_city"] or None, fields["address_postal_code"] or None,
                fields["phone"] or None,
            )
            code = auth.generate_code()
            db.set_verification_code(user_id, auth.hash_code(code), auth.expiry_timestamp())
            email_sender.send_verification_code(fields["email"], code)
            session["pending_verification_user_id"] = user_id
            if is_json:
                return jsonify({"ok": True, "redirect": url_for("verify_email")})
            return redirect(url_for("verify_email"))

        if is_json:
            return jsonify({"error": error}), 400

    body = auth_pages.SIGNUP_PAGE.format(
        error=f'<div class="error">{error}</div>' if error else "",
        country_options=countries.options_html(fields["country"]), **fields,
    )
    return auth_pages.render("Sign Up", body)


@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    user_id = session.get("pending_verification_user_id")
    if not user_id:
        return auth_pages.render("Verify Email", auth_pages.VERIFY_EMAIL_RESUME_PAGE.format(error=""))

    user = db.get_user_by_id(user_id)
    if not user or user["email_verified"]:
        session.pop("pending_verification_user_id", None)
        return redirect(url_for("login"))

    error = ""
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if auth.is_expired(user["verification_code_expires_at"]):
            error = "That code has expired. Request a new one below."
        elif user["verification_attempts"] >= auth.MAX_VERIFICATION_ATTEMPTS:
            error = "Too many incorrect attempts. Request a new code below."
        elif not auth.code_matches(code, user["verification_code_hash"]):
            db.record_failed_verification_attempt(user_id)
            error = "Incorrect code."
        else:
            db.mark_email_verified(user_id)
            session.pop("pending_verification_user_id", None)
            session["user_id"] = user_id
            return redirect(url_for("index"))

    body = auth_pages.VERIFY_EMAIL_PAGE.format(
        masked_email=_mask_email(user["email"]),
        error=f'<div class="error">{error}</div>' if error else "",
    )
    return auth_pages.render("Verify Email", body)


@app.post("/verify-email/resend")
def resend_verification_code():
    user_id = session.get("pending_verification_user_id")
    if user_id:
        user = db.get_user_by_id(user_id)
        if user and not user["email_verified"]:
            wait = auth.seconds_until_resend_allowed(user["verification_code_expires_at"])
            if wait <= 0:
                code = auth.generate_code()
                db.set_verification_code(user_id, auth.hash_code(code), auth.expiry_timestamp())
                email_sender.send_verification_code(user["email"], code)
    return redirect(url_for("verify_email"))


@app.post("/verify-email/resume")
def verify_email_resume():
    username = (request.form.get("username") or "").strip()
    user = db.get_user_by_username(username)
    if not user or user["email_verified"] or not user["email"]:
        body = auth_pages.VERIFY_EMAIL_RESUME_PAGE.format(
            error='<div class="error">No pending verification found for that username.</div>',
        )
        return auth_pages.render("Verify Email", body)
    session["pending_verification_user_id"] = user["id"]
    return redirect(url_for("verify_email"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    identifier = ""
    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        user = db.get_user_by_username(identifier) or db.get_user_by_email(identifier)
        # Always the same response whether or not an account was found --
        # anything else would let this form be used to probe which
        # usernames/emails exist.
        if user and user["email"]:
            code = auth.generate_code()
            db.set_reset_code(user["id"], auth.hash_code(code), auth.expiry_timestamp())
            email_sender.send_reset_code(user["email"], code)
            session["pending_reset_user_id"] = user["id"]
        return redirect(url_for("reset_password", sent=1))

    body = auth_pages.FORGOT_PASSWORD_PAGE.format(error="", identifier=identifier)
    return auth_pages.render("Forgot Password", body)


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    info = '<div class="info">If an account exists, a code has been sent.</div>' if request.args.get("sent") else ""
    error = ""
    if request.method == "POST":
        user_id = session.get("pending_reset_user_id")
        user = db.get_user_by_id(user_id) if user_id else None
        code = (request.form.get("code") or "").strip()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not user or auth.is_expired(user["reset_code_expires_at"]):
            error = "That code is invalid or has expired."
        elif user["reset_attempts"] >= auth.MAX_RESET_ATTEMPTS:
            error = "Too many incorrect attempts. Request a new code."
        elif not auth.code_matches(code, user["reset_code_hash"]):
            db.record_failed_reset_attempt(user_id)
            error = "Incorrect code."
        elif password != confirm_password:
            error = "Passwords don't match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            db.reset_password(user_id, generate_password_hash(password))
            session.pop("pending_reset_user_id", None)
            session["user_id"] = user_id
            return redirect(url_for("index"))

    body = auth_pages.RESET_PASSWORD_PAGE.format(
        info=info, error=f'<div class="error">{error}</div>' if error else "",
    )
    return auth_pages.render("Reset Password", body)


AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_MAX_DIMENSION = (3840, 2160)
AVATAR_STORE_DIMENSION = (512, 512)


def _current_user_or_none():
    # session["user_id"] can outlive the account it points at (e.g. an
    # admin deletes the row while the browser still has a valid signed
    # session cookie) -- callers must handle None rather than assume the
    # row still exists.
    user = db.get_user_by_id(session["user_id"])
    if not user:
        session.clear()
    return user


def _render_profile(**messages):
    user = _current_user_or_none()
    if not user:
        return redirect(url_for("login"))
    return render_template(
        "profile.html", active_page="profile", user=db.public_user_dict(user),
        country_options=countries.options_html(user["country"] or ""),
        country_name=countries.country_name(user["country"]) if user["country"] else None,
        country_flag=countries.flag_emoji(user["country"]) if user["country"] else "",
        **messages,
    )


@app.get("/profile")
def profile():
    return _render_profile(account_error="", account_success="", avatar_error="")


@app.post("/profile/account")
def profile_account():
    user_id = session["user_id"]
    body = request.get_json(force=True) or {}
    fields = {k: (body.get(k) or "").strip() or None for k in
              ("country", "address_line1", "address_city", "address_postal_code", "phone")}
    error = _validate_profile_fields({k: v or "" for k, v in fields.items()})
    if error:
        return jsonify({"error": error}), 400
    db.update_profile_fields(user_id, fields)
    country = fields["country"]
    return jsonify({
        "ok": True,
        "country_name": countries.country_name(country) if country else None,
        "country_flag": countries.flag_emoji(country) if country else "",
    })


@app.post("/profile/avatar")
def profile_avatar():
    user_id = session["user_id"]
    file = request.files.get("avatar")
    if not file or not file.filename:
        return jsonify({"error": "Choose an image file."}), 400
    data = file.read()
    if len(data) > AVATAR_MAX_BYTES:
        return jsonify({"error": "Image must be 5MB or smaller."}), 400

    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))  # verify() invalidates the object -- reopen to actually use it
    except Exception:
        return jsonify({"error": "That doesn't look like a valid image."}), 400

    if img.width > AVATAR_MAX_DIMENSION[0] or img.height > AVATAR_MAX_DIMENSION[1]:
        img.thumbnail(AVATAR_MAX_DIMENSION, Image.LANCZOS)
    img.thumbnail(AVATAR_STORE_DIMENSION, Image.LANCZOS)

    # Re-encode as JPEG rather than storing the raw upload -- strips EXIF and
    # any non-image payload smuggled into the file, and normalizes format
    # regardless of what was uploaded (PNG, WEBP, etc).
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    avatar_updated_at = db.set_avatar(user_id, buf.getvalue(), "image/jpeg")
    return jsonify({"ok": True, "avatar_updated_at": avatar_updated_at})


@app.post("/profile/avatar/delete")
def profile_avatar_delete():
    db.clear_avatar(session["user_id"])
    return jsonify({"ok": True})


@app.get("/avatar/<int:user_id>")
def avatar(user_id):
    avatar_row = db.get_avatar(user_id)
    if not avatar_row:
        return jsonify({"error": "not found"}), 404
    resp = Response(avatar_row["avatar_image"], mimetype=avatar_row["avatar_content_type"])
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route("/profile/change-password", methods=["GET", "POST"])
def profile_change_password():
    user = _current_user_or_none()
    if not user:
        return redirect(url_for("login"))
    error = ""
    success = ""
    if request.method == "POST":
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if not check_password_hash(user["password_hash"], current_password):
            error = "Current password is incorrect."
        elif new_password != confirm_password:
            error = "New passwords don't match."
        elif len(new_password) < 8:
            error = "New password must be at least 8 characters."
        else:
            db.update_password(user["id"], generate_password_hash(new_password))
            success = "Password updated."

    return render_template("change_password.html", active_page="profile", error=error, success=success)

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


# Kept named `index`, still bound to `/` -- login() redirects to
# url_for("index") on success, so renaming this would silently break that.
@app.get("/")
def index():
    return redirect(url_for("overview"))


NAV_ITEMS = [
    {"key": "overview", "label": "Overview", "endpoint": "overview"},
    {"key": "trades", "label": "Trades", "endpoint": "trades_page"},
    {"key": "macros", "label": "Macros", "endpoint": "macros_page"},
    {"key": "calendar", "label": "Calendar", "endpoint": "calendar_page"},
    {"key": "strategy", "label": "Strategy", "endpoint": "strategy_page"},
]


@app.context_processor
def inject_nav():
    return {"nav_items": NAV_ITEMS}


@app.get("/overview")
def overview():
    return render_template("overview.html", active_page="overview")


@app.get("/trades")
def trades_page():
    return render_template("trades.html", active_page="trades")


@app.get("/macros")
def macros_page():
    return render_template("macros.html", active_page="macros")


@app.get("/calendar")
def calendar_page():
    return render_template("calendar.html", active_page="calendar")


@app.get("/strategy")
def strategy_page():
    return render_template("strategy.html", active_page="strategy")


@app.get("/<path:filename>")
def static_asset(filename):
    if filename not in STATIC_ASSETS:
        return jsonify({"error": "not found"}), 404
    # 5 minutes, not longer -- these files change during active development
    # and Render restarts the whole process on every deploy anyway, so
    # there's no real cost to keeping this short. Still cuts every repeat
    # page load's round-trip for these two files to zero within a session,
    # instead of a conditional GET (send_from_directory's default) on every
    # single load.
    return send_from_directory(HERE, filename, max_age=300)


@app.get("/api/trades")
def api_list():
    return jsonify(db.list_trades(session["user_id"]))


@app.post("/api/trades")
def api_create():
    try:
        fields = clean_payload(request.get_json(force=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    new_id = db.insert_trade(session["user_id"], fields)
    return jsonify({"id": new_id}), 201


@app.put("/api/trades/<int:trade_id>")
def api_update(trade_id):
    try:
        fields = clean_payload(request.get_json(force=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    db.update_trade(session["user_id"], trade_id, fields)
    return jsonify({"ok": True})


@app.delete("/api/trades/<int:trade_id>")
def api_delete(trade_id):
    db.delete_trade(session["user_id"], trade_id)
    return jsonify({"ok": True})


IMPORT_MAX_BYTES = 20 * 1024 * 1024


def _parse_import_sample(filename, data):
    if filename.lower().endswith(".xlsx"):
        return import_trades.parse_xlsx_sample(data)
    return import_trades.parse_csv_sample(data)


def _parse_import_full(filename, data):
    if filename.lower().endswith(".xlsx"):
        return import_trades.parse_full_xlsx(data)
    return import_trades.parse_full_csv(data)


@app.post("/api/import/preview")
def api_import_preview():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Choose a CSV or XLSX file."}), 400
    data = file.read()
    if len(data) > IMPORT_MAX_BYTES:
        return jsonify({"error": "File must be 20MB or smaller."}), 400
    try:
        result = _parse_import_sample(file.filename, data)
    except Exception as e:
        return jsonify({"error": f"Couldn't read that file: {e}"}), 400
    result["target_fields"] = import_trades.TARGET_FIELDS
    return jsonify(result)


@app.post("/api/import/commit")
def api_import_commit():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Choose a CSV or XLSX file."}), 400
    data = file.read()
    if len(data) > IMPORT_MAX_BYTES:
        return jsonify({"error": "File must be 20MB or smaller."}), 400

    try:
        mapping = json.loads(request.form.get("mapping", "{}"))
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid mapping."}), 400
    has_header = request.form.get("has_header") == "true"
    risk_pnl_is_percent = request.form.get("risk_pnl_is_percent") == "true"

    try:
        rows = _parse_import_full(file.filename, data)
    except Exception as e:
        return jsonify({"error": f"Couldn't read that file: {e}"}), 400
    if has_header:
        rows = rows[1:]

    ok, failed, errors = 0, 0, []
    for i, row in enumerate(rows):
        if not any(str(c).strip() for c in row):
            continue  # skip blank rows
        try:
            raw_fields = import_trades.row_to_trade_fields(row, mapping, risk_pnl_is_percent)
            fields = clean_payload(raw_fields)
            db.insert_trade(session["user_id"], fields)
            ok += 1
        except (ValueError, KeyError, TypeError) as e:
            failed += 1
            errors.append({"row": i + 1, "error": str(e)})

    return jsonify({"inserted": ok, "failed": failed, "errors": errors[:50]})


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
    return jsonify(db.list_strategies(session["user_id"]))


@app.post("/api/strategies")
def api_create_strategy():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    new_id = db.create_strategy(session["user_id"], name, body.get("description") or "")
    return jsonify({"id": new_id}), 201


@app.put("/api/strategies/<int:strategy_id>")
def api_update_strategy(strategy_id):
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    db.update_strategy(session["user_id"], strategy_id, name, body.get("description") or "")
    return jsonify({"ok": True})


@app.delete("/api/strategies/<int:strategy_id>")
def api_delete_strategy(strategy_id):
    db.delete_strategy(session["user_id"], strategy_id)
    return jsonify({"ok": True})


@app.post("/api/strategies/<int:strategy_id>/assign-untagged")
def api_assign_untagged(strategy_id):
    count = db.assign_untagged_trades(session["user_id"], strategy_id)
    return jsonify({"count": count})


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
        report = macro_sync.sync()
    except Exception as e:
        return jsonify({"error": f"macro sync failed: {e}"}), 502
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


@app.get("/api/calendar")
def api_calendar():
    return jsonify(calendar_view.upcoming_events())


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
    strategy_text = "\n\n".join(f"### {s['name']}\n{s['description'] or ''}" for s in db.list_strategies(session["user_id"]))
    try:
        analysis = ai_bias.run_bias_check(pair, strategy_text, comparison, chart_link)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"AI call failed: {e}"}), 502
    return jsonify({"analysis": analysis})


if __name__ == "__main__":
    if db.count_users() == 0:
        print("No users yet -- run 'python create_user.py <username>' to create your login.")
    else:
        print("Password protection is ON.")
    # Local runs stay on 127.0.0.1 (loopback only) unless HOST is set explicitly.
    # A real deployment (Render, etc.) runs this via gunicorn instead, not this block.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5151))
    # threaded=True so one slow request (an external market-data/news call,
    # a Turso round-trip) doesn't block every other request behind it --
    # confirmed by hand that a single-threaded dev server made even static
    # CSS/JS take 1-2s to load while a ticker poll was in flight.
    app.run(host=host, port=port, debug=False, threaded=True)
