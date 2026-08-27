"""Shared Turso (libSQL) access for the live trade-entry dashboard."""
import os
import time
from datetime import datetime

import libsql_client

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# One CREATE TABLE per statement -- libsql_client has no executescript(),
# unlike stdlib sqlite3.
SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        session TEXT,
        pair TEXT,
        direction TEXT,
        risk REAL,
        rr REAL NOT NULL DEFAULT 0,
        pnl REAL NOT NULL DEFAULT 0,
        result TEXT NOT NULL,
        notes TEXT,
        chart_daily TEXT,
        chart_4h TEXT,
        chart_30m TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS macro (
        currency TEXT PRIMARY KEY,
        bias TEXT,
        notes TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
]

MAJOR_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD"]

# Single source of truth for every macro metric the app tracks. Their actual
# table columns (plus a prev_<key> twin for each) are added by the migration
# below rather than spelled out in SCHEMA -- adding a new metric here is
# enough, no separate CREATE TABLE edit needed.
MACRO_METRIC_KEYS = [
    "interest_rate", "cpi_yoy", "cpi_mom", "core_cpi_yoy", "core_ppi_yoy", "core_pce_yoy", "core_pce_mom",
    "unemployment", "employment_change", "retail_sales_yoy", "trade_balance", "current_account", "gdp_yoy",
    "gdp_mom", "pmi",
]


def _to_https(url):
    # The libsql:// scheme talks Hrana-over-websocket, which fails its
    # protocol handshake with this client version -- the https:// scheme is
    # the one confirmed working for every operation this app needs.
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    return url


def _migrate_macro_prev_columns(conn):
    # Metric columns were added to this table over time, after real databases
    # already existed -- CREATE TABLE IF NOT EXISTS won't add columns to a
    # table that's already there, so backfill any missing ones by hand.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(macro)").rows}
    needed = list(MACRO_METRIC_KEYS) + [f"prev_{k}" for k in MACRO_METRIC_KEYS]
    for col in needed:
        if col not in existing:
            conn.execute(f"ALTER TABLE macro ADD COLUMN {col} REAL")


def _migrate_trades_strategy_column(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").rows}
    if "strategy_id" not in existing:
        # Nullable and no FOREIGN KEY constraint on purpose: SQLite/libsql only
        # enforces foreign keys when PRAGMA foreign_keys=ON is set per
        # connection (easy to forget elsewhere in the codebase and get
        # silently-unenforced constraints), and a trade logged before this
        # column existed -- or one that's just discretionary, no formal
        # setup -- has no strategy to point at. NULL means exactly that.
        conn.execute("ALTER TABLE trades ADD COLUMN strategy_id INTEGER")


def _migrate_user_id_columns(conn):
    # Same rationale/pattern as strategy_id above: nullable, no FK, backfilled
    # by hand since these columns were added after real data already existed.
    for table in ("trades", "strategies"):
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").rows}
        if "user_id" not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")


def _migrate_user_profile_columns(conn):
    # Signup/verification/password-reset support, added after real accounts
    # (e.g. felix's) already existed -- every new column is nullable or
    # defaults to "not verified yet" so existing accounts keep working
    # unchanged. No UNIQUE constraint on email: SQLite/libsql can't add one
    # via ALTER TABLE on an existing table, so uniqueness is enforced at the
    # app layer instead (same "no FK, app-level discipline" convention
    # already used for trades.user_id).
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)").rows}
    additions = {
        "first_name": "TEXT",
        "last_name": "TEXT",
        "email": "TEXT",
        "email_verified": "INTEGER NOT NULL DEFAULT 0",
        "verification_code_hash": "TEXT",
        "verification_code_expires_at": "TEXT",
        "verification_attempts": "INTEGER NOT NULL DEFAULT 0",
        "reset_code_hash": "TEXT",
        "reset_code_expires_at": "TEXT",
        "reset_attempts": "INTEGER NOT NULL DEFAULT 0",
    }
    for col, coltype in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")


def _migrate_user_extended_profile_columns(conn):
    # Country/address/phone (all optional, editable from the profile page)
    # and avatar storage -- avatar bytes live directly on this row rather
    # than a separate table since there's only ever one per user; served
    # through its own route (see get_avatar), never embedded in
    # public_user_dict()'s output.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)").rows}
    additions = {
        "country": "TEXT",
        "address_line1": "TEXT",
        "address_city": "TEXT",
        "address_postal_code": "TEXT",
        "phone": "TEXT",
        "avatar_image": "BLOB",
        "avatar_content_type": "TEXT",
        "avatar_updated_at": "TEXT",
    }
    for col, coltype in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")


_schema_ready = False
_client = None


def _ensure_schema(conn):
    # Each statement here is a separate network round-trip to Turso (~300ms),
    # unlike the equivalent local-sqlite PRAGMA/ALTER calls this was ported
    # from, which cost microseconds -- running all of this on every single
    # get_conn() call (matching the old sqlite3 pattern) added several
    # seconds of latency to every API request. Run it once per process
    # instead; a stale flag after a schema change just means restarting the
    # process, same as any other in-memory cache in this app.
    global _schema_ready
    if _schema_ready:
        return
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    _migrate_macro_prev_columns(conn)
    _migrate_trades_strategy_column(conn)
    _migrate_user_id_columns(conn)
    _migrate_user_profile_columns(conn)
    _migrate_user_extended_profile_columns(conn)
    _schema_ready = True


def get_conn():
    # One client reused for the life of the process rather than a fresh one
    # per call: each call also pays a ~1s "cold" first-request cost on top of
    # the usual ~300ms per query, and this app's gunicorn setup (Procfile) is
    # a single sync worker handling one request at a time, so there's no
    # concurrent-access risk to reusing it.
    global _client
    if _client is None:
        # Read lazily, not as a module-level constant -- server.py imports
        # this module before it calls its own _load_dotenv(), so capturing
        # these at import time would always see them unset locally.
        url = os.environ.get("TURSO_DATABASE_URL")
        token = os.environ.get("TURSO_AUTH_TOKEN")
        if not url or not token:
            raise RuntimeError(
                "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set -- this app "
                "stores everything in Turso now, there is no local file fallback."
            )
        _client = libsql_client.create_client_sync(url=_to_https(url), auth_token=token)
    _ensure_schema(_client)
    return _client


def close():
    # libsql_client's background executor thread is not a daemon thread, so
    # skipping this leaves any short-lived script (create_user.py, a one-off
    # migration) hanging indefinitely after it's actually done -- Python
    # won't exit while a non-daemon thread is still alive. Not needed by the
    # live server itself, which runs until the process is killed anyway.
    global _client
    if _client is not None:
        _client.close()
        _client = None


def derive_result(rr):
    # Based on realized R-multiple, not raw PnL -- a trade that closed
    # slightly positive but well under your planned 1R isn't a real "win"
    # in an R-multiple system, it's a breakeven-ish outcome. Always
    # computed, never manually overridden (see insert_trade/update_trade).
    if rr < 0:
        return "LOSS"
    if rr <= 1:
        return "BE"
    return "WIN"


def row_to_dict(row):
    d = row.asdict()
    try:
        dt = datetime.strptime(d["date"], "%Y-%m-%d").date()
        d["day"] = DAY_NAMES[dt.weekday()]
        d["year"] = dt.year
    except (ValueError, TypeError):
        d["day"] = None
        d["year"] = None
    charts = []
    for label, key in (("Daily", "chart_daily"), ("4H", "chart_4h"), ("30M", "chart_30m")):
        link = d.get(key)
        if link:
            charts.append({"label": label, "link": link, "img": snapshot_image_url(link)})
    d["charts"] = charts
    d["strategy_name"] = d.get("strategy_name") or "No strategy"
    return d


def snapshot_image_url(tv_link):
    import re
    if not tv_link:
        return None
    m = re.search(r"/x/([A-Za-z0-9]+)", str(tv_link))
    if not m:
        return tv_link if str(tv_link).lower().endswith((".png", ".jpg", ".jpeg")) else None
    sid = m.group(1)
    return f"https://s3.tradingview.com/snapshots/{sid[0].lower()}/{sid}.png"


def list_trades(user_id):
    conn = get_conn()
    rows = conn.execute("""
        SELECT trades.*, strategies.name AS strategy_name
        FROM trades LEFT JOIN strategies ON strategies.id = trades.strategy_id
        WHERE trades.user_id = ?
        ORDER BY trades.date ASC, trades.id ASC
    """, (user_id,)).rows
    return [row_to_dict(r) for r in rows]


def _strategy_id_or_none(fields):
    raw = fields.get("strategy_id")
    return int(raw) if raw not in (None, "") else None


def insert_trade(user_id, fields):
    now = datetime.now().isoformat(timespec="seconds")
    pnl = float(fields.get("pnl") or 0)
    result = derive_result(float(fields.get("rr") or 0))
    conn = get_conn()
    rs = conn.execute(
        """INSERT INTO trades (date, session, pair, direction, risk, rr, pnl, result, notes,
                                chart_daily, chart_4h, chart_30m, strategy_id, user_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            _strategy_id_or_none(fields), user_id, now, now,
        ),
    )
    new_id = rs.last_insert_rowid
    return new_id


def update_trade(user_id, trade_id, fields):
    now = datetime.now().isoformat(timespec="seconds")
    pnl = float(fields.get("pnl") or 0)
    result = derive_result(float(fields.get("rr") or 0))
    conn = get_conn()
    conn.execute(
        """UPDATE trades SET date=?, session=?, pair=?, direction=?, risk=?, rr=?, pnl=?, result=?,
               notes=?, chart_daily=?, chart_4h=?, chart_30m=?, strategy_id=?, updated_at=?
           WHERE id=? AND user_id=?""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            _strategy_id_or_none(fields), now, trade_id, user_id,
        ),
    )


def delete_trade(user_id, trade_id):
    conn = get_conn()
    conn.execute("DELETE FROM trades WHERE id=? AND user_id=?", (trade_id, user_id))


# ---------- generic settings (key/value, global) ----------

def get_setting(key, default=None):
    conn = get_conn()
    rows = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).rows
    return rows[0]["value"] if rows else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# ---------- strategies (per-user) ----------

def list_strategies(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM strategies WHERE user_id=? ORDER BY created_at ASC", (user_id,)).rows
    return [r.asdict() for r in rows]


def create_strategy(user_id, name, description):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    rs = conn.execute(
        "INSERT INTO strategies (name, description, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, description, user_id, now, now),
    )
    new_id = rs.last_insert_rowid
    return new_id


def delete_strategy(user_id, strategy_id):
    conn = get_conn()
    conn.execute("DELETE FROM strategies WHERE id=? AND user_id=?", (strategy_id, user_id))


# ---------- macro snapshot (global, shared by every user) ----------

# Same global data read by every user on every Macros-page load (often
# several times per load -- fundamentals, the workspace, and the bias
# table each call list_macro() independently) but changed only by an
# explicit FRED sync or a manual edit -- worth a short in-process cache to
# cut repeat Turso round-trips (~300ms each), invalidated on any write.
_macro_cache = None
_MACRO_CACHE_TTL_SECONDS = 30


def list_macro():
    global _macro_cache
    if _macro_cache is not None:
        cached_at, cached_result = _macro_cache
        if time.time() - cached_at < _MACRO_CACHE_TTL_SECONDS:
            return cached_result
    conn = get_conn()
    rows = {r["currency"]: r.asdict() for r in conn.execute("SELECT * FROM macro").rows}
    result = []
    for c in MAJOR_CURRENCIES:
        blank = {"currency": c, "bias": None, "notes": None, "updated_at": None}
        for key in MACRO_METRIC_KEYS:
            blank[key] = None
            blank[f"prev_{key}"] = None
        result.append(rows.get(c) or blank)
    _macro_cache = (time.time(), result)
    return result


def upsert_macro(currency, fields):
    global _macro_cache
    _macro_cache = None
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    existing_rows = conn.execute("SELECT * FROM macro WHERE currency=?", (currency,)).rows
    existing = existing_rows[0].asdict() if existing_rows else {}

    # Keep one step of history per metric -- whatever the value *was* moves
    # into prev_* only when the incoming value actually changes it, so the UI
    # can show a real latest-vs-prev comparison instead of just a snapshot.
    new_values, prev_values = {}, {}
    for key in MACRO_METRIC_KEYS:
        new_val = fields[key] if key in fields else existing.get(key)
        old_val = existing.get(key)
        new_values[key] = new_val
        if new_val != old_val and old_val is not None:
            prev_values[key] = old_val
        else:
            prev_values[key] = existing.get(f"prev_{key}")

    current_cols = MACRO_METRIC_KEYS
    prev_cols = [f"prev_{k}" for k in MACRO_METRIC_KEYS]
    all_cols = ["currency"] + current_cols + prev_cols + ["bias", "notes", "updated_at"]
    placeholders = ",".join("?" * len(all_cols))
    update_clause = ",".join(f"{c}=excluded.{c}" for c in current_cols + prev_cols + ["bias", "notes", "updated_at"])
    values = (
        [currency] + [new_values[k] for k in current_cols] + [prev_values[k] for k in current_cols]
        + [fields.get("bias"), fields.get("notes"), now]
    )
    conn.execute(
        f"INSERT INTO macro ({','.join(all_cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(currency) DO UPDATE SET {update_clause}",
        values,
    )


# ---------- users ----------

# Fields safe to hand back to a browser -- everything else on the users row
# (password_hash, every verification/reset code+expiry+attempts column) must
# never reach a template context or jsonify() call.
_PUBLIC_USER_FIELDS = [
    "id", "username", "first_name", "last_name", "email", "email_verified", "created_at",
    "country", "address_line1", "address_city", "address_postal_code", "phone", "avatar_updated_at",
]


def public_user_dict(user):
    return {k: user.get(k) for k in _PUBLIC_USER_FIELDS}


def get_user_by_username(username):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users WHERE username=?", (username,)).rows
    return rows[0].asdict() if rows else None


def get_user_by_id(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).rows
    return rows[0].asdict() if rows else None


def get_user_by_email(email):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users WHERE email=?", (email,)).rows
    return rows[0].asdict() if rows else None


def create_user(username, password_hash, first_name=None, last_name=None, email=None,
                 country=None, address_line1=None, address_city=None, address_postal_code=None, phone=None):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    rs = conn.execute(
        """INSERT INTO users (username, password_hash, first_name, last_name, email,
                               country, address_line1, address_city, address_postal_code, phone, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, password_hash, first_name, last_name, email,
         country, address_line1, address_city, address_postal_code, phone, now),
    )
    new_id = rs.last_insert_rowid
    return new_id


def update_profile_fields(user_id, fields):
    # fields: any subset of {country, address_line1, address_city, address_postal_code, phone}
    cols = list(fields.keys())
    if not cols:
        return
    set_clause = ",".join(f"{c}=?" for c in cols)
    conn = get_conn()
    conn.execute(f"UPDATE users SET {set_clause} WHERE id=?", [fields[c] for c in cols] + [user_id])


def set_avatar(user_id, image_bytes, content_type):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE users SET avatar_image=?, avatar_content_type=?, avatar_updated_at=? WHERE id=?",
        (image_bytes, content_type, now, user_id),
    )


def clear_avatar(user_id):
    conn = get_conn()
    conn.execute(
        "UPDATE users SET avatar_image=NULL, avatar_content_type=NULL, avatar_updated_at=NULL WHERE id=?",
        (user_id,),
    )


def get_avatar(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT avatar_image, avatar_content_type, avatar_updated_at FROM users WHERE id=?", (user_id,)
    ).rows
    if not rows or rows[0]["avatar_image"] is None:
        return None
    return rows[0].asdict()


def count_users():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM users").rows[0]["n"]
    return n


def set_verification_code(user_id, code_hash, expires_at):
    conn = get_conn()
    conn.execute(
        "UPDATE users SET verification_code_hash=?, verification_code_expires_at=?, verification_attempts=0 WHERE id=?",
        (code_hash, expires_at, user_id),
    )


def record_failed_verification_attempt(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET verification_attempts = verification_attempts + 1 WHERE id=?", (user_id,))


def mark_email_verified(user_id):
    conn = get_conn()
    conn.execute(
        """UPDATE users SET email_verified=1, verification_code_hash=NULL,
               verification_code_expires_at=NULL, verification_attempts=0 WHERE id=?""",
        (user_id,),
    )


def set_reset_code(user_id, code_hash, expires_at):
    conn = get_conn()
    conn.execute(
        "UPDATE users SET reset_code_hash=?, reset_code_expires_at=?, reset_attempts=0 WHERE id=?",
        (code_hash, expires_at, user_id),
    )


def record_failed_reset_attempt(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET reset_attempts = reset_attempts + 1 WHERE id=?", (user_id,))


def reset_password(user_id, password_hash):
    conn = get_conn()
    conn.execute(
        """UPDATE users SET password_hash=?, reset_code_hash=NULL,
               reset_code_expires_at=NULL, reset_attempts=0 WHERE id=?""",
        (password_hash, user_id),
    )


def update_password(user_id, password_hash):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
