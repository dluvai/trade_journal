"""
Optional automatic macro sync from FRED (Federal Reserve Economic Data).

Free, no API key needed -- uses the same public CSV export endpoint FRED's
own graph pages use (fred.stlouisfed.org/graph/fredgraph.csv), which is
openly served (robots.txt has no restrictions). Verified series freshness by
hand before wiring anything in -- see the comments below for what's covered
and what's deliberately left out.

Coverage: USD, EUR, GBP, JPY only, and not every metric for every one of
those:
  - PMI is never included -- it's licensed by S&P Global/ISM, nobody
    redistributes it for free.
  - AUD, NZD, CHF are not covered -- FRED doesn't carry good free mirrors
    for them across these metrics.
  - EUR unemployment is not included -- FRED's Euro-area harmonised
    unemployment mirror (the OECD MEI series) stopped updating in 2023;
    every variant checked was stale by 2+ years. Left manual rather than
    showing a silently outdated number.
  - GBP and JPY CPI use a World Bank ANNUAL inflation series, not monthly --
    FRED's monthly OECD mirrors for both were stale (GBP: 2024, JPY: 2021).
    The annual figure updates once a year but is at least current within
    the last year, which beats a multi-year-old monthly print.

Run standalone to see exactly what it would fetch:
    python fred_sync.py
"""
import csv
import io
import urllib.request
from datetime import datetime, timedelta

import db

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# metric -> (FRED series id, mode)
#   'level' = use the latest value as-is (already the right unit, e.g. a rate or a %)
#   'yoy'   = the series is an index/level; compute % change vs ~1 year earlier
SERIES = {
    "USD": {
        "interest_rate": ("FEDFUNDS", "level"),           # Federal funds effective rate
        "gdp_yoy":       ("GDPC1", "yoy"),                 # Real GDP, quarterly
        "unemployment":  ("UNRATE", "level"),
        "cpi_yoy":       ("CPIAUCSL", "yoy"),               # CPI index, monthly
    },
    "EUR": {
        "interest_rate": ("ECBDFR", "level"),               # ECB deposit facility rate
        "gdp_yoy":       ("CLVMEURSCAB1GQEA19", "yoy"),     # Real GDP, Euro Area 19, quarterly
        "cpi_yoy":       ("CP0000EZ19M086NEST", "yoy"),     # HICP index, monthly
    },
    "GBP": {
        "interest_rate": ("IR3TIB01GBM156N", "level"),      # 3-month interbank rate (BoE-rate proxy)
        "gdp_yoy":       ("NGDPRSAXDCGBQ", "yoy"),          # Real GDP, quarterly
        "unemployment":  ("LRHUTTTTGBM156S", "level"),
        "cpi_yoy":       ("FPCPITOTLZGGBR", "level"),       # World Bank annual inflation
    },
    "JPY": {
        "interest_rate": ("IRSTCI01JPM156N", "level"),      # interbank call rate (BOJ-rate proxy)
        "gdp_yoy":       ("JPNRGDPEXP", "yoy"),             # Real GDP, quarterly
        "unemployment":  ("LRHUTTTTJPM156S", "level"),
        "cpi_yoy":       ("FPCPITOTLZGJPN", "level"),       # World Bank annual inflation
    },
}

PROXY_NOTES = {
    ("GBP", "interest_rate"): "3-month interbank rate used as a proxy for BoE policy stance, not the literal Bank Rate.",
    ("JPY", "interest_rate"): "Interbank call rate used as a proxy for BOJ policy stance, not the literal policy rate.",
    ("GBP", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
    ("JPY", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
}


def _fetch_series(series_id):
    url = FRED_CSV.format(series_id=series_id)
    # FRED's edge silently stalls requests carrying a generic browser-style
    # User-Agent (confirmed by hand: identical request with "curl/8.0" returns
    # instantly, "Mozilla/5.0" hangs until the socket times out) -- so we ask
    # honestly as what we are rather than spoofing a browser.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8")
    rows = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # header row
    for row in reader:
        if len(row) != 2:
            continue
        date_str, value_str = row
        if value_str in ("", ".", "NA"):
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            v = float(value_str)
        except ValueError:
            continue
        rows.append((d, v))
    return rows


def _yoy(rows):
    if len(rows) < 2:
        return (rows[-1][0], None) if rows else (None, None)
    latest_date, latest_value = rows[-1]
    target = latest_date - timedelta(days=365)
    prior_date, prior_value = min(rows[:-1], key=lambda r: abs((r[0] - target).days))
    if abs((prior_date - target).days) > 60 or prior_value == 0:
        return latest_date, None
    return latest_date, round((latest_value / prior_value - 1) * 100, 2)


def fetch_metric(series_id, mode):
    rows = _fetch_series(series_id)
    if not rows:
        return None, None
    if mode == "level":
        d, v = rows[-1]
        return d, round(v, 2)
    return _yoy(rows)


def sync(currencies=None, dry_run=False):
    """Pull the covered metrics for each currency and merge them into the
    macro table -- manually-entered fields not covered here (PMI, bias,
    notes, and anything for uncovered currencies) are left untouched."""
    currencies = currencies or list(SERIES.keys())
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}
    report = {}

    for ccy in currencies:
        metrics = SERIES.get(ccy, {})
        detail = {}
        fetched = {}
        for metric, (series_id, mode) in metrics.items():
            try:
                as_of, value = fetch_metric(series_id, mode)
            except Exception as e:
                detail[metric] = {"series_id": series_id, "error": str(e)}
                continue
            note = PROXY_NOTES.get((ccy, metric))
            detail[metric] = {
                "series_id": series_id, "as_of": str(as_of) if as_of else None,
                "value": value, "note": note,
            }
            if value is not None:
                fetched[metric] = value

        if fetched and not dry_run:
            existing = existing_by_ccy.get(ccy, {})
            merged = {
                "interest_rate": existing.get("interest_rate"),
                "cpi_yoy": existing.get("cpi_yoy"),
                "gdp_yoy": existing.get("gdp_yoy"),
                "unemployment": existing.get("unemployment"),
                "pmi": existing.get("pmi"),
                "bias": existing.get("bias"),
                "notes": existing.get("notes"),
            }
            merged.update(fetched)
            db.upsert_macro(ccy, merged)

        report[ccy] = detail

    return report


if __name__ == "__main__":
    import json
    print(json.dumps(sync(dry_run=True), indent=2))
