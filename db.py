"""Shared SQLite access for the live trade-entry dashboard."""
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

# Overridable so a hosted deployment can point this at a persistent disk
# (e.g. Render's mounted volume) instead of the app's own ephemeral folder.
DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent / "trades.db")))

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
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
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS macro (
    currency TEXT PRIMARY KEY,
    bias TEXT,
    notes TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MAJOR_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF"]

# Single source of truth for every macro metric the app tracks. Their actual
# table columns (plus a prev_<key> twin for each) are added by the migration
# below rather than spelled out in SCHEMA -- adding a new metric here is
# enough, no separate CREATE TABLE edit needed.
MACRO_METRIC_KEYS = [
    "interest_rate", "cpi_yoy", "cpi_mom", "core_cpi_yoy", "core_ppi_yoy", "core_pce_yoy",
    "unemployment", "retail_sales_yoy", "trade_balance", "current_account", "gdp_yoy", "pmi",
]


def _migrate_macro_prev_columns(conn):
    # Metric columns were added to this table over time, after real databases
    # already existed -- CREATE TABLE IF NOT EXISTS won't add columns to a
    # table that's already there, so backfill any missing ones by hand.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(macro)")}
    needed = list(MACRO_METRIC_KEYS) + [f"prev_{k}" for k in MACRO_METRIC_KEYS]
    for col in needed:
        if col not in existing:
            conn.execute(f"ALTER TABLE macro ADD COLUMN {col} REAL")
    conn.commit()


def _migrate_trades_strategy_column(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(trades)")}
    if "strategy_id" not in existing:
        # Nullable and no FOREIGN KEY constraint on purpose: SQLite only
        # enforces foreign keys when PRAGMA foreign_keys=ON is set per
        # connection (easy to forget elsewhere in the codebase and get
        # silently-unenforced constraints), and a trade logged before this
        # column existed -- or one that's just discretionary, no formal
        # setup -- has no strategy to point at. NULL means exactly that.
        conn.execute("ALTER TABLE trades ADD COLUMN strategy_id INTEGER")
    conn.commit()


def get_conn():
    # If DB_PATH points at a directory that doesn't exist yet (e.g. a Render
    # disk mount path set before the disk was actually attached), create it
    # rather than letting sqlite3 fail to open the file entirely.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate_macro_prev_columns(conn)
    _migrate_trades_strategy_column(conn)
    return conn


def derive_result(pnl):
    if pnl > 0:
        return "WIN"
    if pnl < 0:
        return "LOSS"
    return "BE"


def row_to_dict(row):
    d = dict(row)
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


def list_trades():
    conn = get_conn()
    rows = conn.execute("""
        SELECT trades.*, strategies.name AS strategy_name
        FROM trades LEFT JOIN strategies ON strategies.id = trades.strategy_id
        ORDER BY trades.date ASC, trades.id ASC
    """).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def _strategy_id_or_none(fields):
    raw = fields.get("strategy_id")
    return int(raw) if raw not in (None, "") else None


def insert_trade(fields):
    now = datetime.now().isoformat(timespec="seconds")
    pnl = float(fields.get("pnl") or 0)
    result = fields.get("result") or derive_result(pnl)
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO trades (date, session, pair, direction, risk, rr, pnl, result, notes,
                                chart_daily, chart_4h, chart_30m, strategy_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            _strategy_id_or_none(fields), now, now,
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_trade(trade_id, fields):
    now = datetime.now().isoformat(timespec="seconds")
    pnl = float(fields.get("pnl") or 0)
    result = fields.get("result") or derive_result(pnl)
    conn = get_conn()
    conn.execute(
        """UPDATE trades SET date=?, session=?, pair=?, direction=?, risk=?, rr=?, pnl=?, result=?,
               notes=?, chart_daily=?, chart_4h=?, chart_30m=?, strategy_id=?, updated_at=? WHERE id=?""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            _strategy_id_or_none(fields), now, trade_id,
        ),
    )
    conn.commit()
    conn.close()


def delete_trade(trade_id):
    conn = get_conn()
    conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    conn.commit()
    conn.close()


# ---------- generic settings (key/value) ----------

def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------- strategies (list) ----------

def list_strategies():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM strategies ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_strategy(name, description):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO strategies (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, description, now, now),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def delete_strategy(strategy_id):
    conn = get_conn()
    conn.execute("DELETE FROM strategies WHERE id=?", (strategy_id,))
    conn.commit()
    conn.close()


# ---------- macro snapshot ----------

def list_macro():
    conn = get_conn()
    rows = {r["currency"]: dict(r) for r in conn.execute("SELECT * FROM macro")}
    conn.close()
    result = []
    for c in MAJOR_CURRENCIES:
        blank = {"currency": c, "bias": None, "notes": None, "updated_at": None}
        for key in MACRO_METRIC_KEYS:
            blank[key] = None
            blank[f"prev_{key}"] = None
        result.append(rows.get(c) or blank)
    return result


def upsert_macro(currency, fields):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    existing_row = conn.execute("SELECT * FROM macro WHERE currency=?", (currency,)).fetchone()
    existing = dict(existing_row) if existing_row else {}

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
    conn.commit()
    conn.close()
