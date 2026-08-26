"""
One-time import: reads ../Felix Trade Journal.xlsx and loads every trade into
the shared Turso database under one account, so a fresh account has its
full history to start from.

Run once, for a given username (must already exist -- see create_user.py):
    python migrate_from_xlsx.py <username>

Safe to re-run: it refuses to run again if that user already has trades,
unless you pass --force (which wipes and re-imports just their trades).
"""
import re
import sys

import openpyxl

import db
from xlsx_import import SOURCE, extract_sheet


def main():
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    if len(args) != 1:
        sys.exit("Usage: python migrate_from_xlsx.py <username> [--force]")
    username = args[0]

    try:
        user = db.get_user_by_username(username)
        if not user:
            sys.exit(f"No user named '{username}' -- create one first with create_user.py.")
        user_id = user["id"]

        conn = db.get_conn()
        existing = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE user_id=?", (user_id,)).rows[0]["n"]
        if existing and not force:
            print(f"'{username}' already has {existing} trades. Re-run with --force to wipe and re-import.")
            return
        if force:
            conn.execute("DELETE FROM trades WHERE user_id=?", (user_id,))

        wb = openpyxl.load_workbook(SOURCE, data_only=True)
        imported = 0
        for sheet_name in wb.sheetnames:
            m = re.search(r"(20\d{2})", sheet_name)
            year = int(m.group(1)) if m else None
            for t in extract_sheet(wb[sheet_name], year):
                db.insert_trade(user_id, t)
                imported += 1

        print(f"Imported {imported} trades from {SOURCE.name} for '{username}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
