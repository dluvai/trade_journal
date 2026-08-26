"""
Copies every trade from your local trades.db up to a hosted deployment of
this dashboard, via its own API (POST /api/trades for each one).

Run this YOURSELF -- it needs your dashboard password, which should never
be typed into a script file or shared with anyone (Claude included). Set
it as an environment variable in your own terminal, then run this:

    Windows PowerShell:
        $env:REMOTE_URL = "https://felixtrades.onrender.com"
        $env:REMOTE_PASSWORD = "your-actual-password"
        python seed_remote.py

Safe to re-run against an empty remote, but NOT idempotent -- running it
twice against a remote that already has trades will duplicate them. If
you need to start clean, clear the remote trades first (delete them via
its own UI, or wipe its trades.db) before re-running this.
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
    remote_password = os.environ.get("REMOTE_PASSWORD")

    if not remote_url:
        sys.exit("Set REMOTE_URL first, e.g.:\n  $env:REMOTE_URL = \"https://felixtrades.onrender.com\"")

    session = requests.Session()

    if remote_password:
        resp = session.post(f"{remote_url}/login", data={"password": remote_password}, timeout=20)
        if "Incorrect password" in resp.text:
            sys.exit("Login failed -- check REMOTE_PASSWORD.")
    # If REMOTE_PASSWORD isn't set, we proceed unauthenticated -- fine only
    # if the remote has no DASHBOARD_PASSWORD configured either.

    trades = db.list_trades()
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


if __name__ == "__main__":
    main()
