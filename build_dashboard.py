"""
Regenerates dashboard.html -- a self-contained, portable snapshot of the
live dashboard -- from trades.db (the same database the live server reads
and writes). Useful for sharing a read-only copy without running server.py.

Run this any time you want a fresh snapshot:
    python build_dashboard.py
"""
import json
from datetime import datetime
from pathlib import Path

import db

HERE = Path(__file__).parent
TEMPLATE = HERE / "dashboard_template.html"
OUTPUT = HERE / "dashboard.html"


def main():
    trades = db.list_trades()
    years = sorted({t["year"] for t in trades if t["year"]})

    data = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_file": db.DB_PATH.name,
            "years": years,
        },
        "trades": trades,
    }

    template = TEMPLATE.read_text(encoding="utf-8")
    output = template.replace("/*__TRADE_DATA__*/", json.dumps(data))
    OUTPUT.write_text(output, encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(trades)} trades ({data['meta']['years']}).")


if __name__ == "__main__":
    main()
