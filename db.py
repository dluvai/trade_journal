"""Shared Turso (libSQL) access for the live trade-entry dashboard."""
import json
import os
import time
from datetime import datetime

import libsql_client

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# One statement per entry since libsql_client has no executescript().
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
    """CREATE TABLE IF NOT EXISTS macro_bias_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        currency TEXT NOT NULL,
        bias TEXT NOT NULL,
        set_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trading_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        stripe_subscription_id TEXT NOT NULL UNIQUE,
        stripe_price_id TEXT,
        tier_key TEXT,
        status TEXT NOT NULL,
        current_period_end TEXT,
        cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        description TEXT,
        features TEXT,
        unit_amount_cents INTEGER NOT NULL,
        stripe_product_id TEXT NOT NULL,
        stripe_price_id TEXT NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # The column's own UNIQUE is case-sensitive (SQLite default collation), which let "felix" and
    # "Felix" both be registered as separate accounts -- this index enforces true case-insensitive
    # uniqueness at the database level, closing the race condition an app-level check alone can't.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase ON users(username COLLATE NOCASE)",
    """CREATE TABLE IF NOT EXISTS weekly_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        week_start TEXT NOT NULL,
        week_end TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
]

MAJOR_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD"]

# Single source of truth for macro metrics -- the migration below adds the actual (and prev_*) columns, so a new metric only needs adding here.
MACRO_METRIC_KEYS = [
    "interest_rate", "cpi_yoy", "cpi_mom", "core_cpi_yoy", "core_ppi_yoy", "ppi_yoy", "core_pce_yoy", "core_pce_mom",
    "unemployment", "employment_change", "retail_sales_yoy", "trade_balance", "current_account",
    "current_account_pct_gdp", "gdp_yoy", "gdp_mom", "pmi",
]


def _to_https(url):
    # Rewrites libsql:// to https://, since Hrana-over-websocket fails its handshake with this client version.
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    return url


def _migrate_macro_prev_columns(conn):
    # Backfills columns by hand since CREATE TABLE IF NOT EXISTS won't add them to an already-existing table.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(macro)").rows}
    needed = list(MACRO_METRIC_KEYS) + [f"prev_{k}" for k in MACRO_METRIC_KEYS]
    for col in needed:
        if col not in existing:
            conn.execute(f"ALTER TABLE macro ADD COLUMN {col} REAL")


def _migrate_trades_strategy_column(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").rows}
    if "strategy_id" not in existing:
        # Nullable, no FK on purpose -- foreign keys need a per-connection PRAGMA easy to forget elsewhere,
        # and pre-existing/strategy-less trades genuinely have nothing to point at.
        conn.execute("ALTER TABLE trades ADD COLUMN strategy_id INTEGER")


def _migrate_trades_account_column(conn):
    # Same rationale as strategy_id -- trades already has data, so the new account_id link still needs an ALTER TABLE.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").rows}
    if "account_id" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN account_id INTEGER")


def _migrate_trades_entered_time_column(conn):
    # Optional "HH:MM" clock time -- nullable since it's new and historical trades can never have it retroactively.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").rows}
    if "entered_time" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN entered_time TEXT")


def _migrate_user_id_columns(conn):
    # Same pattern as strategy_id: nullable, no FK, hand-backfilled since real data predates the column.
    for table in ("trades", "strategies"):
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").rows}
        if "user_id" not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")


def _migrate_user_profile_columns(conn):
    # New columns are all nullable/defaulted so pre-existing accounts keep working; email uniqueness
    # is enforced at the app layer since ALTER TABLE can't add a UNIQUE constraint.
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
    # Avatar bytes live directly on the users row (one per user, no separate table needed) and are
    # served via their own route, never in public_user_dict().
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


def _migrate_user_billing_columns(conn):
    # is_admin is a real permission bit, not an env-var/hardcoded-id check -- deliberate even
    # though only one account will ever have it set today. stripe_customer_id is created lazily
    # on first checkout attempt, not at signup, since most users may never subscribe.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)").rows}
    additions = {"is_admin": "INTEGER NOT NULL DEFAULT 0", "stripe_customer_id": "TEXT"}
    for col, coltype in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")


_schema_ready = False
_client = None


def _ensure_schema(conn):
    # Runs once per process (not per get_conn()) since each migration statement costs ~300ms over the network, unlike the local-sqlite calls this replaced.
    global _schema_ready
    if _schema_ready:
        return
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    _migrate_macro_prev_columns(conn)
    _migrate_trades_strategy_column(conn)
    _migrate_trades_account_column(conn)
    _migrate_trades_entered_time_column(conn)
    _migrate_user_id_columns(conn)
    _migrate_user_profile_columns(conn)
    _migrate_user_extended_profile_columns(conn)
    _migrate_user_billing_columns(conn)
    _schema_ready = True


def get_conn():
    # Client is reused for the process lifetime to avoid the ~1s cold-start cost per call.
    global _client
    if _client is None:
        # Read lazily, not at import time, since server.py imports this module before loading its own .env.
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
    # Needed by short-lived scripts (not the live server) since libsql_client's background thread isn't a daemon and would otherwise hang the process.
    global _client
    if _client is not None:
        _client.close()
        _client = None


def derive_result(rr):
    # Result is derived from realized R-multiple (not raw PnL), and always computed -- never manually overridden.
    if rr < 0:
        return "LOSS"
    if rr == 0:
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
    d["account_name"] = d.get("account_name") or "Unassigned"
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
        SELECT trades.*, strategies.name AS strategy_name, trading_accounts.name AS account_name
        FROM trades
        LEFT JOIN strategies ON strategies.id = trades.strategy_id
        LEFT JOIN trading_accounts ON trading_accounts.id = trades.account_id
        WHERE trades.user_id = ?
        ORDER BY trades.date ASC, trades.id ASC
    """, (user_id,)).rows
    return [row_to_dict(r) for r in rows]


def _strategy_id_or_none(fields):
    raw = fields.get("strategy_id")
    return int(raw) if raw not in (None, "") else None


def _account_id_or_none(fields):
    raw = fields.get("account_id")
    return int(raw) if raw not in (None, "") else None


def insert_trade(user_id, fields):
    now = datetime.now().isoformat(timespec="seconds")
    pnl = float(fields.get("pnl") or 0)
    result = derive_result(float(fields.get("rr") or 0))
    conn = get_conn()
    rs = conn.execute(
        """INSERT INTO trades (date, session, pair, direction, risk, rr, pnl, result, notes,
                                chart_daily, chart_4h, chart_30m, strategy_id, account_id, entered_time, user_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            _strategy_id_or_none(fields), _account_id_or_none(fields), fields.get("entered_time") or None,
            user_id, now, now,
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
               notes=?, chart_daily=?, chart_4h=?, chart_30m=?, strategy_id=?, account_id=?, entered_time=?, updated_at=?
           WHERE id=? AND user_id=?""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            _strategy_id_or_none(fields), _account_id_or_none(fields), fields.get("entered_time") or None,
            now, trade_id, user_id,
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


def update_strategy(user_id, strategy_id, name, description):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE strategies SET name=?, description=?, updated_at=? WHERE id=? AND user_id=?",
        (name, description, now, strategy_id, user_id),
    )


# ---------- trading accounts (per-user) ----------

def list_trading_accounts(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trading_accounts WHERE user_id=? ORDER BY created_at ASC", (user_id,)
    ).rows
    return [r.asdict() for r in rows]


def create_trading_account(user_id, name):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    rs = conn.execute(
        "INSERT INTO trading_accounts (name, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, user_id, now, now),
    )
    return rs.last_insert_rowid


def update_trading_account(user_id, account_id, name):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE trading_accounts SET name=?, updated_at=? WHERE id=? AND user_id=?",
        (name, now, account_id, user_id),
    )


def delete_trading_account(user_id, account_id):
    conn = get_conn()
    conn.execute("DELETE FROM trading_accounts WHERE id=? AND user_id=?", (account_id, user_id))


def assign_untagged_trades(user_id, strategy_id):
    """Bulk-tag every trade this user has logged with no strategy at all
    (strategy_id IS NULL) onto the given strategy in one shot -- retagging
    trades one at a time through the edit modal doesn't scale once you
    already have a real trade history and are only now starting to tag by
    strategy. Returns how many rows were actually touched."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    rs = conn.execute(
        "UPDATE trades SET strategy_id=?, updated_at=? WHERE user_id=? AND strategy_id IS NULL",
        (strategy_id, now, user_id),
    )
    return rs.rows_affected


# ---------- macro snapshot (global, shared by every user) ----------

# In-process cache since every Macros-page load calls list_macro() several times but the data only changes on sync/edit.
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

    # Append-only history, kept separate from the `macro` row itself (which only ever holds the
    # current value) -- this is the only place a bias call becomes scoreable against trades later.
    new_bias = fields.get("bias")
    if new_bias and new_bias != existing.get("bias"):
        conn.execute(
            "INSERT INTO macro_bias_history (currency, bias, set_at) VALUES (?, ?, ?)",
            (currency, new_bias, now),
        )

    # Keeps one step of history per metric so the UI can show a real latest-vs-prev comparison.
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


def list_bias_history():
    # All currencies at once, ordered oldest-first -- the frontend does its own "most recent
    # call as of this trade's date" lookup per currency, so there's no need for a currency filter here.
    conn = get_conn()
    rows = conn.execute("SELECT currency, bias, set_at FROM macro_bias_history ORDER BY set_at ASC").rows
    return [r.asdict() for r in rows]


# ---------- AI weekly review ----------

def create_weekly_review(user_id, week_start, week_end, content):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    rs = conn.execute(
        "INSERT INTO weekly_reviews (user_id, week_start, week_end, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, week_start, week_end, content, now),
    )
    return rs.last_insert_rowid


def list_weekly_reviews(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM weekly_reviews WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).rows
    return [r.asdict() for r in rows]


# ---------- users ----------

# Only these fields are browser-safe -- password_hash and every reset/verification column must never reach a template or jsonify().
_PUBLIC_USER_FIELDS = [
    "id", "username", "first_name", "last_name", "email", "email_verified", "created_at",
    "country", "address_line1", "address_city", "address_postal_code", "phone", "avatar_updated_at",
]


def public_user_dict(user):
    return {k: user.get(k) for k in _PUBLIC_USER_FIELDS}


def get_user_by_username(username):
    # Case-insensitive on purpose -- "felix" and "Felix" are the same account, not two.
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).rows
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


def delete_user(user_id):
    # Only used to roll back a just-created signup, so a plain single-row delete is safe (no trades/strategies exist yet).
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def update_email(user_id, email):
    # Always resets verification on email change, since a new address is unverified by definition.
    conn = get_conn()
    conn.execute(
        """UPDATE users SET email=?, email_verified=0, verification_code_hash=NULL,
               verification_code_expires_at=NULL, verification_attempts=0 WHERE id=?""",
        (email, user_id),
    )


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
    return now


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


# ---------- billing ----------

def set_admin(user_id, is_admin=True):
    conn = get_conn()
    conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin else 0, user_id))


def set_stripe_customer_id(user_id, stripe_customer_id):
    conn = get_conn()
    conn.execute("UPDATE users SET stripe_customer_id=? WHERE id=?", (stripe_customer_id, user_id))


def get_user_by_stripe_customer_id(stripe_customer_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users WHERE stripe_customer_id=?", (stripe_customer_id,)).rows
    return rows[0].asdict() if rows else None


def search_users(query):
    conn = get_conn()
    like = f"%{query}%"
    rows = conn.execute(
        """SELECT * FROM users WHERE username LIKE ? OR email LIKE ? OR first_name LIKE ? OR last_name LIKE ?
           ORDER BY created_at DESC LIMIT 50""",
        (like, like, like, like),
    ).rows
    return [r.asdict() for r in rows]


def user_diagnostics(user_id):
    conn = get_conn()
    trades = conn.execute("SELECT COUNT(*) AS n, MAX(date) AS last_date FROM trades WHERE user_id=?", (user_id,)).rows[0]
    accounts = conn.execute("SELECT COUNT(*) AS n FROM trading_accounts WHERE user_id=?", (user_id,)).rows[0]
    strategies = conn.execute("SELECT COUNT(*) AS n FROM strategies WHERE user_id=?", (user_id,)).rows[0]
    return {
        "trade_count": trades["n"],
        "last_trade_date": trades["last_date"],
        "account_count": accounts["n"],
        "strategy_count": strategies["n"],
    }


def upsert_subscription(user_id, stripe_subscription_id, stripe_price_id, tier_key, status,
                         current_period_end, cancel_at_period_end):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """INSERT INTO subscriptions
               (user_id, stripe_subscription_id, stripe_price_id, tier_key, status,
                current_period_end, cancel_at_period_end, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(stripe_subscription_id) DO UPDATE SET
               stripe_price_id=excluded.stripe_price_id, tier_key=excluded.tier_key,
               status=excluded.status, current_period_end=excluded.current_period_end,
               cancel_at_period_end=excluded.cancel_at_period_end, updated_at=excluded.updated_at""",
        (user_id, stripe_subscription_id, stripe_price_id, tier_key, status,
         current_period_end, 1 if cancel_at_period_end else 0, now, now),
    )


def get_subscription_for_user(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM subscriptions WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user_id,)
    ).rows
    return rows[0].asdict() if rows else None


# ---------- plans (global, admin-managed) ----------

def _plan_row_to_dict(row):
    d = row.asdict()
    d["features"] = json.loads(d["features"] or "[]")
    return d


def list_plans(include_archived=False):
    conn = get_conn()
    if include_archived:
        rows = conn.execute("SELECT * FROM plans ORDER BY created_at ASC").rows
    else:
        rows = conn.execute("SELECT * FROM plans WHERE archived=0 ORDER BY created_at ASC").rows
    return [_plan_row_to_dict(r) for r in rows]


def subscription_kpis():
    # Joins on tier_key (the stable slug), not stripe_price_id, since a price stops matching once its plan is archived/recreated.
    conn = get_conn()
    row = conn.execute(
        """SELECT COUNT(*) AS active_count, COALESCE(SUM(plans.unit_amount_cents), 0) AS mrr_cents
           FROM subscriptions
           JOIN plans ON plans.key = subscriptions.tier_key
           WHERE subscriptions.status = 'active'"""
    ).rows[0]
    return {"active_count": row["active_count"], "mrr_cents": row["mrr_cents"]}


def get_plan_by_key(key):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM plans WHERE key=?", (key,)).rows
    return _plan_row_to_dict(rows[0]) if rows else None


def get_plan_by_id(plan_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).rows
    return _plan_row_to_dict(rows[0]) if rows else None


def create_plan(key, label, description, features, unit_amount_cents, stripe_product_id, stripe_price_id):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    rs = conn.execute(
        """INSERT INTO plans
               (key, label, description, features, unit_amount_cents, stripe_product_id, stripe_price_id,
                archived, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (key, label, description, json.dumps(features or []), unit_amount_cents,
         stripe_product_id, stripe_price_id, now, now),
    )
    return rs.last_insert_rowid


def update_plan(plan_id, label, description, features):
    # Display-copy-only: price/product/key are immutable post-creation (Stripe Prices can't change amount).
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE plans SET label=?, description=?, features=?, updated_at=? WHERE id=?",
        (label, description, json.dumps(features or []), now, plan_id),
    )


def archive_plan(plan_id):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute("UPDATE plans SET archived=1, updated_at=? WHERE id=?", (now, plan_id))
