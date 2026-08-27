"""
Optional automatic macro sync from the OECD's SDMX API (sdmx.oecd.org).

Free, no API key needed -- OECD's public REST data endpoint returns
structured SDMX-JSON for government-backed national-accounts/labour/price
statistics. Verified series freshness by hand before wiring anything in --
see the comments below for what's covered and what's deliberately left out.

This exists alongside fred_sync.py, not instead of it: FRED's non-US
coverage is thin (annual-only CPI for several currencies, no unemployment
series at all for NZD/CHF). OECD fills exactly those specific gaps where
it was verified live to be a genuine improvement -- it does not replace
FRED for currencies/metrics FRED already covers well (USD everything,
EUR cpi_yoy's Euro-area aggregate, CHF/JPY cpi_yoy). macro_sync.py is the
module that decides, per (currency, metric), which of this file or
fred_sync.py to call -- this file only knows how to fetch OECD series.

Coverage, and not every metric for every currency:
  - cpi_yoy: GBR, CAN, AUS (monthly) and NZD (quarterly -- New Zealand has
    never published a monthly CPI, so quarterly is NZ's own real cadence,
    not a compromise). CHF and JPY were checked and came back worse than
    or not clearly better than FRED's existing fallback (Dec 2025 and June
    2021 respectively at verification time) -- they stay on FRED. EUR's
    cpi_yoy stays on FRED too: FRED already has a genuine Euro-area-wide
    aggregate there, and swapping it for a single-country (Germany) proxy
    would be a step down in representativeness, not up in freshness.
  - unemployment: GBR, CAN, AUS (monthly), EUR via Germany as a
    single-country proxy (monthly -- FRED's own EUR unemployment mirror
    stopped updating in 2023, so this is net-new capability, not a
    like-for-like swap), and NZD/CHF (quarterly -- both are net-new; FRED
    has no series for either currency at all today).
  - Everything else (interest_rate, gdp_yoy, PMI, core_*, employment_change,
    retail_sales_yoy, trade_balance, current_account) is not covered here.
    GDP YoY's likely-right OECD dataset was found but every country
    checked came back capped at the same stale quarter, which reads as a
    pinned dataset version rather than a genuine gap -- not resolved yet,
    left for a follow-up rather than guessed at. The rest are either
    already well-served by FRED or are USD-only concepts with no free
    cross-country equivalent found anywhere.

Run standalone to see exactly what it would fetch:
    python oecd_sync.py
"""
import concurrent.futures
import json
import urllib.request

import db

OECD_URL = "https://sdmx.oecd.org/public/rest/data/{dataset_id}/{dimension_key}?dimensionAtObservation=AllDimensions&format=jsondata"

# currency -> metric -> (dataset_id, dimension_key, parse_mode)
#   parse_mode is documentation, not dispatch: every series used here is
#   already the percentage/rate we want straight out of OECD (CPI's "GY"
#   transformation is already year-over-year growth, and the labour-force
#   unemployment series is already a rate) -- there's no ratio math to do
#   like FRED's _yoy()/_mom(), just "take the latest observation."
OECD_SERIES = {
    # Dict keys are currency codes (matching db.MAJOR_CURRENCIES /
    # fred_sync.SERIES), not the ISO country codes used inside the OECD
    # dimension-key strings themselves (e.g. GBP the currency vs. GBR the
    # country in "GBR.M.N.CPI...").
    "GBP": {
        "cpi_yoy": ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "GBR.M.N.CPI.PA._T.N.GY", "yoy_direct"),
        "unemployment": ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", "GBR..._Z.Y._T.Y_GE15..M", "level"),
    },
    "CAD": {
        "cpi_yoy": ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "CAN.M.N.CPI.PA._T.N.GY", "yoy_direct"),
        "unemployment": ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", "CAN..._Z.Y._T.Y_GE15..M", "level"),
    },
    "AUD": {
        "cpi_yoy": ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "AUS.M.N.CPI.PA._T.N.GY", "yoy_direct"),
        "unemployment": ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", "AUS..._Z.Y._T.Y_GE15..M", "level"),
    },
    "EUR": {
        "unemployment": ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", "DEU..._Z.Y._T.Y_GE15..M", "level"),
    },
    "NZD": {
        "cpi_yoy": ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "NZL.Q.N.CPI.PA._T.N.GY", "yoy_direct"),
        "unemployment": ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", "NZL..._Z.Y._T.Y_GE15..Q", "level"),
    },
    "CHF": {
        "unemployment": ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", "CHE..._Z.Y._T.Y_GE15..Q", "level"),
    },
}

OECD_NOTES = {
    ("EUR", "unemployment"): "Germany used as a single-country proxy for the euro area, not a true euro-area-wide aggregate.",
    ("NZD", "unemployment"): "Quarterly, not monthly -- New Zealand's own official reporting cadence, not a data gap.",
    ("CHF", "unemployment"): "Quarterly, not monthly -- Swiss LFS is published quarterly. FRED has no CHF unemployment series at all.",
    ("NZD", "cpi_yoy"): "Quarterly, not monthly -- New Zealand has never published a monthly CPI.",
}


def _fetch_oecd_series(dataset_id, dimension_key):
    url = OECD_URL.format(dataset_id=dataset_id, dimension_key=dimension_key)
    # OECD's edge blocks Python's default urllib User-Agent with a 403
    # (confirmed by hand: identical request with "curl/8.0" succeeds
    # instantly) -- the same class of bug that already bit FRED and Resend
    # this app talks to, so this header is required, not precautionary.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        envelope = json.loads(resp.read().decode("utf-8"))
    data = envelope["data"]

    observations = data["dataSets"][0]["observations"]
    if not observations:
        return []
    obs_dims = data["structures"][0]["dimensions"]["observation"]
    time_dim_index = next(i for i, d in enumerate(obs_dims) if d["id"] == "TIME_PERIOD")
    time_values = obs_dims[time_dim_index]["values"]

    rows = []
    for key, value_list in observations.items():
        if not value_list or value_list[0] is None:
            continue
        time_idx = int(key.split(":")[time_dim_index])
        period = time_values[time_idx]["id"]
        rows.append((period, float(value_list[0])))
    # Period strings sort correctly lexicographically within one query since
    # every observation here shares one frequency ("YYYY-MM" or "YYYY-QN"),
    # never both mixed in the same series.
    rows.sort(key=lambda r: r[0])
    return rows


def fetch_metric(dataset_id, dimension_key, parse_mode):
    rows = _fetch_oecd_series(dataset_id, dimension_key)
    if not rows:
        return None, None
    period, value = rows[-1]
    return period, round(value, 2)


def _fetch_one(job):
    ccy, metric, spec = job
    dataset_id, dimension_key, parse_mode = spec
    try:
        as_of, value = fetch_metric(dataset_id, dimension_key, parse_mode)
    except Exception as e:
        return ccy, metric, dataset_id, None, None, str(e)
    return ccy, metric, dataset_id, as_of, value, None


def sync(currencies=None, dry_run=False):
    """Pull the covered metrics for each currency and merge them into the
    macro table -- manually-entered fields not covered here (PMI, bias,
    notes, and anything for uncovered currencies) are left untouched."""
    currencies = currencies or list(OECD_SERIES.keys())
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}
    report = {ccy: {} for ccy in currencies}
    fetched_by_ccy = {ccy: {} for ccy in currencies}

    jobs = [(ccy, metric, spec) for ccy in currencies for metric, spec in OECD_SERIES.get(ccy, {}).items()]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        results = list(pool.map(_fetch_one, jobs))

    for ccy, metric, dataset_id, as_of, value, error in results:
        if error is not None:
            report[ccy][metric] = {"dataset_id": dataset_id, "error": error}
            continue
        report[ccy][metric] = {
            "dataset_id": dataset_id, "as_of": str(as_of) if as_of else None,
            "value": value, "note": OECD_NOTES.get((ccy, metric)),
        }
        if value is not None:
            fetched_by_ccy[ccy][metric] = value

    if not dry_run:
        for ccy in currencies:
            fetched = fetched_by_ccy[ccy]
            if not fetched:
                continue
            existing = existing_by_ccy.get(ccy, {})
            merged = {key: existing.get(key) for key in db.MACRO_METRIC_KEYS}
            merged["bias"] = existing.get("bias")
            merged["notes"] = existing.get("notes")
            merged.update(fetched)
            db.upsert_macro(ccy, merged)

    return report


if __name__ == "__main__":
    print(json.dumps(sync(dry_run=True), indent=2))
