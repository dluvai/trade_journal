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
    interest_rate REAL,
    cpi_yoy REAL,
    gdp_yoy REAL,
    unemployment REAL,
    pmi REAL,
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


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
    rows = conn.execute("SELECT * FROM trades ORDER BY date ASC, id ASC").fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def insert_trade(fields):
    now = datetime.now().isoformat(timespec="seconds")
    pnl = float(fields.get("pnl") or 0)
    result = fields.get("result") or derive_result(pnl)
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO trades (date, session, pair, direction, risk, rr, pnl, result, notes,
                                chart_daily, chart_4h, chart_30m, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            now, now,
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
               notes=?, chart_daily=?, chart_4h=?, chart_30m=?, updated_at=? WHERE id=?""",
        (
            fields["date"], fields.get("session"), fields.get("pair"), fields.get("direction"),
            float(fields["risk"]) if fields.get("risk") not in (None, "") else None,
            float(fields.get("rr") or 0), pnl, result, fields.get("notes"),
            fields.get("chart_daily"), fields.get("chart_4h"), fields.get("chart_30m"),
            now, trade_id,
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
        result.append(rows.get(c) or {
            "currency": c, "interest_rate": None, "cpi_yoy": None, "gdp_yoy": None,
            "unemployment": None, "pmi": None, "bias": None, "notes": None, "updated_at": None,
        })
    return result


def upsert_macro(currency, fields):
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """INSERT INTO macro (currency, interest_rate, cpi_yoy, gdp_yoy, unemployment, pmi, bias, notes, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(currency) DO UPDATE SET
               interest_rate=excluded.interest_rate, cpi_yoy=excluded.cpi_yoy, gdp_yoy=excluded.gdp_yoy,
               unemployment=excluded.unemployment, pmi=excluded.pmi, bias=excluded.bias,
               notes=excluded.notes, updated_at=excluded.updated_at""",
        (
            currency, fields.get("interest_rate"), fields.get("cpi_yoy"), fields.get("gdp_yoy"),
            fields.get("unemployment"), fields.get("pmi"), fields.get("bias"), fields.get("notes"), now,
        ),
    )
    conn.commit()
    conn.close()
