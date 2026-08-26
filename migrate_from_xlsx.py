"""
One-time import: reads ../Felix Trade Journal.xlsx and loads every trade into
trades.db (SQLite), so the live dashboard has your full history to start from.

Run once:
    python migrate_from_xlsx.py

Safe to re-run: it refuses to run again if trades.db already has trades,
unless you pass --force (which wipes and re-imports).
"""
import re
import sys

import openpyxl

import db
from xlsx_import import SOURCE, extract_sheet


def main():
    force = "--force" in sys.argv

    conn = db.get_conn()
    existing = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
    if existing and not force:
        print(f"trades.db already has {existing} trades. Re-run with --force to wipe and re-import.")
        conn.close()
        return
    if force:
        conn.execute("DELETE FROM trades")
        conn.commit()
    conn.close()

    wb = openpyxl.load_workbook(SOURCE, data_only=True)
    imported = 0
    for sheet_name in wb.sheetnames:
        m = re.search(r"(20\d{2})", sheet_name)
        year = int(m.group(1)) if m else None
        for t in extract_sheet(wb[sheet_name], year):
            db.insert_trade(t)
            imported += 1

    print(f"Imported {imported} trades from {SOURCE.name} into {db.DB_PATH.name}.")


if __name__ == "__main__":
    main()
