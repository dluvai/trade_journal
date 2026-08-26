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
import time
import urllib.parse
import urllib.request
from datetime import date

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

_cache = {}


def _fetch_release_dates(release_id, api_key):
    params = {
        "release_id": release_id,
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": date.today().isoformat(),
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "limit": 1,
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    dates = data.get("release_dates", [])
    return dates[0]["date"] if dates else None


def next_release_dates():
    """{metric_key: 'YYYY-MM-DD' or None} for every FRED-covered USD metric.
    Returns {} entirely if FRED_API_KEY isn't set -- callers should treat
    that as "calendar feature not enabled" rather than an error."""
    import os
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
                seen_releases[release_id] = _fetch_release_dates(release_id, api_key)
            except Exception:
                seen_releases[release_id] = None
        result[metric] = seen_releases[release_id]

    _cache["next_dates"] = (time.time(), result)
    return result


if __name__ == "__main__":
    print(json.dumps(next_release_dates(), indent=2))
