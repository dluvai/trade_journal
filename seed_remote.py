"""
Copies every trade for one account up to a hosted deployment of this
dashboard, via its own API (POST /api/trades for each one).

Largely obsolete since the Turso migration: local dev and every deployment
now read/write the same shared database, so there's no separate "local"
copy of your trades left to seed from. This is kept around only in case you
ever need to seed a *second*, separate environment (e.g. a staging Turso
database) -- not part of normal day-to-day use anymore.

Run this YOURSELF -- it needs your dashboard password, which should never
be typed into a script file or shared with anyone (Claude included). Set
it as an environment variable in your own terminal, then run this:

    Windows PowerShell:
        $env:REMOTE_URL = "https://felixtrades.onrender.com"
        $env:REMOTE_USERNAME = "your-username"
        $env:REMOTE_PASSWORD = "your-actual-password"
        python seed_remote.py

Safe to re-run against an empty remote, but NOT idempotent -- running it
twice against a remote that already has trades will duplicate them. If
you need to start clean, clear the remote trades first (delete them via
its own UI) before re-running this.
"""
import os
import sys

import requests

import db

FIELDS = [
    "date", "session", "pair", "direction", "risk", "rr", "pnl", "result",
    "notes", "chart_daily", "chart_4h", "chart_30m",
]


def main():
    remote_url = os.environ.get("REMOTE_URL", "").rstrip("/")
    remote_username = os.environ.get("REMOTE_USERNAME")
    remote_password = os.environ.get("REMOTE_PASSWORD")

    if not remote_url:
        sys.exit("Set REMOTE_URL first, e.g.:\n  $env:REMOTE_URL = \"https://felixtrades.onrender.com\"")
    if not remote_username or not remote_password:
        sys.exit("Set REMOTE_USERNAME and REMOTE_PASSWORD first.")

    try:
        user = db.get_user_by_username(remote_username)
        if not user:
            sys.exit(f"No local user named '{remote_username}'.")

        session = requests.Session()
        resp = session.post(f"{remote_url}/login", data={"username": remote_username, "password": remote_password}, timeout=20)
        if "Incorrect username or password" in resp.text:
            sys.exit("Login failed -- check REMOTE_USERNAME/REMOTE_PASSWORD.")

        trades = db.list_trades(user["id"])
        print(f"Seeding {len(trades)} trades to {remote_url} ...")

        ok, failed = 0, 0
        for t in trades:
            payload = {k: t.get(k) for k in FIELDS}
            r = session.post(f"{remote_url}/api/trades", json=payload, timeout=20)
            if r.status_code == 201:
                ok += 1
            else:
                failed += 1
                print(f"  FAILED {t['date']} {t.get('pair')}: {r.status_code} {r.text[:200]}")

        print(f"Done. {ok} succeeded, {failed} failed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
