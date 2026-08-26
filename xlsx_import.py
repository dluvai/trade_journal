"""
Excel journal parsing -- used once by migrate_from_xlsx.py to seed trades.db.
trades.db is the system of record after that; this module is not on the
regular read path anymore (see build_dashboard.py, which now exports from
the database instead of the workbook).
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE.parent / "Felix Trade Journal.xlsx"

DAY_FIX = {
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
    "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday",
}
SESSION_FIX = {
    "london": "London", "newyork": "New York", "new york": "New York", "asia": "Asia",
}
DIRECTION_FIX = {
    "long": "Long", "short": "Short", "bullish": "Long", "bearish": "Short",
}


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return date(1899, 12, 30) + timedelta(days=int(value))
    if isinstance(value, str):
        v = value.strip()
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    return None


def snapshot_image_url(tv_link):
    """TradingView 'x/<ID>/' snapshot links resolve (via og:image) to
    s3.tradingview.com/snapshots/<first-char-lower>/<ID>.png -- derive it
    directly so the import doesn't have to fetch each page."""
    if not tv_link:
        return None
    m = re.search(r"/x/([A-Za-z0-9]+)", str(tv_link))
    if not m:
        return None
    sid = m.group(1)
    return f"https://s3.tradingview.com/snapshots/{sid[0].lower()}/{sid}.png"


def find_col(header_row, *needles):
    """Find a column index (1-based) whose header contains any of the given substrings."""
    for cell in header_row:
        if cell.value is None:
            continue
        text = str(cell.value).strip().lower()
        for needle in needles:
            if needle in text:
                return cell.column
    return None


def extract_sheet(ws, year):
    header_row = ws[2]
    col_date = find_col(header_row, "date")
    col_day = find_col(header_row, "day")
    col_pair = find_col(header_row, "pair")
    col_risk = find_col(header_row, "risk")
    col_direction = find_col(header_row, "direction")
    col_rr = find_col(header_row, "rr")
    col_pnl = find_col(header_row, "pnl")
    col_result = find_col(header_row, "result")
    col_notes = find_col(header_row, "notes")
    col_daily = find_col(header_row, "daily")
    col_4h = find_col(header_row, "4hr", "4h")
    col_30m = find_col(header_row, "30min", "30m")
    # Session column header is unreliable ("London" literal, or "Session"); it's always
    # the column right after Day in this workbook's layout.
    col_session = col_day + 1

    trades = []
    for r in range(3, ws.max_row + 1):
        raw_date = ws.cell(row=r, column=col_date).value
        d = parse_date(raw_date)
        if d is None:
            continue
        result = ws.cell(row=r, column=col_result).value
        if not result:
            continue
        result = str(result).strip().upper()
        if result not in ("WIN", "LOSS", "BE"):
            continue

        day_raw = str(ws.cell(row=r, column=col_day).value or "").strip()
        session_raw = str(ws.cell(row=r, column=col_session).value or "").strip()
        direction_raw = str(ws.cell(row=r, column=col_direction).value or "").strip()

        pnl = ws.cell(row=r, column=col_pnl).value
        rr = ws.cell(row=r, column=col_rr).value
        risk = ws.cell(row=r, column=col_risk).value
        notes = ws.cell(row=r, column=col_notes).value if col_notes else None

        charts = []
        for label, col in (("Daily", col_daily), ("4H", col_4h), ("30M", col_30m)):
            if not col:
                continue
            link = ws.cell(row=r, column=col).value
            img = snapshot_image_url(link)
            if img:
                charts.append({"label": label, "link": str(link).strip(), "img": img})
        charts_by_label = {c["label"]: c["link"] for c in charts}

        trades.append({
            "date": d.isoformat(),
            "year": year,
            "session": SESSION_FIX.get(session_raw.lower(), session_raw or None),
            "pair": str(ws.cell(row=r, column=col_pair).value or "").strip().upper() or None,
            "direction": DIRECTION_FIX.get(direction_raw.lower(), direction_raw or None),
            "risk": float(risk) if isinstance(risk, (int, float)) else None,
            "rr": float(rr) if isinstance(rr, (int, float)) else 0.0,
            "pnl": float(pnl) if isinstance(pnl, (int, float)) else 0.0,
            "result": result,
            "notes": str(notes).strip() if notes else "",
            "chart_daily": charts_by_label.get("Daily"),
            "chart_4h": charts_by_label.get("4H"),
            "chart_30m": charts_by_label.get("30M"),
        })
    return trades
