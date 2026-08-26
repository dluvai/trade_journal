"""
Next-release dates for the USD macro metrics, from FRED's official release
calendar API -- a different endpoint than fred_sync.py's no-key CSV export,
this one requires a free API key (sign up at
https://fredaccount.stlouisfed.org/apikeys, takes a minute, no cost).

Set FRED_API_KEY as an environment variable to enable this -- same pattern
as DASHBOARD_PASSWORD/ANTHROPIC_API_KEY elsewhere in this app: never typed
into a script, never seen by anyone but you.

Only covers metrics that are an actual scheduled statistical release. Left
out on purpose:
  - interest_rate: an FOMC decision isn't a "release" in FRED's sense, and
    every non-Fed central bank's meeting calendar would need sourcing (and
    keeping up to date) one institution at a time -- not attempted here.
  - pmi: not a FRED release either (S&P Global/ISM publish it themselves).
  - Every non-USD metric: no equivalent free official calendar API found for
    the other six currencies' statistical agencies.

Release IDs verified by hand against https://fred.stlouisfed.org/release?rid=<id>:
  CPI & core CPI = 10, core PPI = 46, core PCE = 54, unemployment = 50,
  retail sales = 9, trade balance = 51, current account = 49, GDP = 53.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

API_BASE = "https://api.stlouisfed.org/fred/release/dates"

METRIC_RELEASE_IDS = {
    "cpi_yoy": 10,
    "cpi_mom": 10,
    "core_cpi_yoy": 10,
    "core_ppi_yoy": 46,
    "core_pce_yoy": 54,
    "unemployment": 50,
    "retail_sales_yoy": 9,
    "trade_balance": 51,
    "current_account": 49,
    "gdp_yoy": 53,
}

# One label per release_id, not per metric -- cpi_yoy/cpi_mom/core_cpi_yoy
# all share release_id 10 (they're the same CPI report), so a calendar
# grouped by release_id shows it once instead of three duplicate rows.
RELEASE_LABELS = {
    10: "CPI report",
    46: "Core PPI",
    54: "Core PCE",
    50: "Employment report",
    9: "Retail sales",
    51: "Trade balance",
    49: "Current account",
    53: "GDP",
}

_cache = {}


def _fetch_release_dates(release_id, api_key, limit=1):
    params = {
        "release_id": release_id,
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": date.today().isoformat(),
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "limit": limit,
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [d["date"] for d in data.get("release_dates", [])]


def next_release_dates():
    """{metric_key: 'YYYY-MM-DD' or None} for every FRED-covered USD metric.
    Returns {} entirely if FRED_API_KEY isn't set -- callers should treat
    that as "calendar feature not enabled" rather than an error."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return {}

    hit = _cache.get("next_dates")
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]

    result = {}
    seen_releases = {}
    for metric, release_id in METRIC_RELEASE_IDS.items():
        if release_id not in seen_releases:
            try:
                dates = _fetch_release_dates(release_id, api_key)
                seen_releases[release_id] = dates[0] if dates else None
            except Exception:
                seen_releases[release_id] = None
        result[metric] = seen_releases[release_id]

    _cache["next_dates"] = (time.time(), result)
    return result


def upcoming_events(days_ahead=14):
    """[{date, currency, label}] for every USD release due in the next
    `days_ahead` days -- the building block for a real weekly/upcoming
    calendar view, as opposed to next_release_dates()'s single-date-per-
    metric shape (good for a metric card, useless for a calendar)."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return []

    hit = _cache.get("upcoming")
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]

    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    for release_id, label in RELEASE_LABELS.items():
        try:
            dates = _fetch_release_dates(release_id, api_key, limit=5)
        except Exception:
            continue
        for d in dates:
            d_obj = datetime.strptime(d, "%Y-%m-%d").date()
            if today <= d_obj <= cutoff:
                events.append({"date": d, "currency": "USD", "label": label})

    _cache["upcoming"] = (time.time(), events)
    return events


if __name__ == "__main__":
    print(json.dumps(next_release_dates(), indent=2))
    print(json.dumps(upcoming_events(), indent=2))
