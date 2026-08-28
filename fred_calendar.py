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

FRED's release-dates API only returns a date, never a time of day -- but
every one of these releases comes from BLS, BEA, or the Census Bureau, and
all three agencies publish on a fixed, publicly documented schedule: 8:30 AM
Eastern Time, every time, no exceptions (bls.gov/bls/newsrels.htm and
bea.gov's own release schedules both state this explicitly). That's a real,
verifiable institutional convention, not a guess, so it's safe to attach a
time_utc to each event using it.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

API_BASE = "https://api.stlouisfed.org/fred/release/dates"
RELEASE_TIME_ET = (8, 30)

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

# One label per release_id (not per metric) so shared reports like CPI don't show as duplicate rows.
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


# Cached per release_id, not per merged result, so one flaky release only affects itself instead of dragging down every other one's cache.
_release_cache = {}
_RELEASE_TTL_SECONDS = 6 * 3600


def _get_release_dates(release_id, api_key, limit=5):
    hit = _release_cache.get(release_id)
    if hit and time.time() - hit[0] < _RELEASE_TTL_SECONDS:
        return hit[1]
    try:
        dates = _fetch_release_dates(release_id, api_key, limit=limit)
        _release_cache[release_id] = (time.time(), dates)
        return dates
    except Exception:
        if hit:
            return hit[1]  # serve the stale-but-real dates rather than nothing
        return []


def _time_utc_for(d_obj):
    hour, minute = RELEASE_TIME_ET
    local = datetime(d_obj.year, d_obj.month, d_obj.day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(ZoneInfo("UTC")).isoformat()


def next_release_dates():
    """{metric_key: 'YYYY-MM-DD' or None} for every FRED-covered USD metric.
    Returns {} entirely if FRED_API_KEY isn't set -- callers should treat
    that as "calendar feature not enabled" rather than an error."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return {}

    # Fetched in parallel since a cold cache would otherwise mean waiting on up to 8 sequential requests.
    unique_ids = sorted(set(METRIC_RELEASE_IDS.values()))
    with ThreadPoolExecutor(max_workers=len(unique_ids)) as pool:
        fetched = dict(zip(unique_ids, pool.map(lambda rid: _get_release_dates(rid, api_key, limit=1), unique_ids)))

    result = {}
    for metric, release_id in METRIC_RELEASE_IDS.items():
        dates = fetched[release_id]
        result[metric] = dates[0] if dates else None
    return result


def upcoming_events(days_ahead=14):
    """[{date, currency, label}] for every USD release due in the next
    `days_ahead` days -- the building block for a real weekly/upcoming
    calendar view, as opposed to next_release_dates()'s single-date-per-
    metric shape (good for a metric card, useless for a calendar)."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return []

    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    for release_id, label in RELEASE_LABELS.items():
        for d in _get_release_dates(release_id, api_key, limit=5):
            d_obj = datetime.strptime(d, "%Y-%m-%d").date()
            if today <= d_obj <= cutoff:
                events.append({"date": d, "currency": "USD", "label": label, "time_utc": _time_utc_for(d_obj)})
    return events


if __name__ == "__main__":
    print(json.dumps(next_release_dates(), indent=2))
    print(json.dumps(upcoming_events(), indent=2))
