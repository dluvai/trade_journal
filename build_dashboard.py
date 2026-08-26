"""
Regenerates dashboard.html -- a self-contained, portable snapshot of one
account's trades -- from the shared Turso database. Useful for sharing a
read-only copy without running server.py.

Run this any time you want a fresh snapshot:
    python build_dashboard.py <username>
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import db

HERE = Path(__file__).parent
TEMPLATE = HERE / "dashboard_template.html"
OUTPUT = HERE / "dashboard.html"


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python build_dashboard.py <username>")
    username = sys.argv[1]

    try:
        user = db.get_user_by_username(username)
        if not user:
            sys.exit(f"No user named '{username}'.")

        trades = db.list_trades(user["id"])
        years = sorted({t["year"] for t in trades if t["year"]})

        data = {
            "meta": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "source": username,
                "years": years,
            },
            "trades": trades,
        }

        template = TEMPLATE.read_text(encoding="utf-8")
        output = template.replace("/*__TRADE_DATA__*/", json.dumps(data))
        OUTPUT.write_text(output, encoding="utf-8")
        print(f"Wrote {OUTPUT} with {len(trades)} trades ({data['meta']['years']}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
