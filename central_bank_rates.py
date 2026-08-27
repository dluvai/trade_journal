"""
Optional automatic sync for interest_rate, pulled directly from each
central bank's own published data instead of FRED's interbank-rate proxy
(GBP/JPY/AUD/NZD/CHF/CAD) or effective/traded rate (USD, handled separately
in fred_sync.py via DFEDTARU). An interbank rate tracks a central bank's
target closely but isn't literally it -- since currency bias scoring treats
this as a exact fact to compare across pairs, going to the primary source
removes that gap entirely rather than accepting an approximation.

Verified live, by hand, before wiring anything in -- see the currency
comments below for the exact source and what concept each series
represents. Every fetcher here hits a public, unauthenticated,
official-institution endpoint (a central bank's own statistics API/database
export), not a mirror or a scrape of a rendered page.

  - CAD: Bank of Canada's Valet API. V39079 is literally "Target for the
    overnight rate" -- the policy rate the Bank sets directly. Daily.
  - AUD: Reserve Bank of Australia's own F1.1 statistical table (CSV).
    FIRMMCRT is "Cash Rate Target; monthly average" -- averaged within the
    month, so it only differs from the flat target in a month where the
    RBA actually changed it mid-month. Monthly.
  - NZD: Reserve Bank of New Zealand's own B2 wholesale-rates file (XLSX).
    INM.DP1.N is literally the Official Cash Rate (OCR). Daily.
  - CHF: Swiss National Bank's own data portal (CSV). Cube "snboffzisa",
    dimension code "LZ" ("Leitzins") is literally the SNB policy rate,
    introduced in 2019 when the SNB retired its old target-range system.
    Monthly.
  - JPY: Bank of Japan's own Time-Series Data Search API (JSON). Series
    STRDCLUCON is the Uncollateralized Overnight Call Rate -- the rate the
    BOJ's current operating framework directly targets, so unlike the old
    Basic Loan Rate (a mostly-symbolic ceiling rate BOJ hasn't relied on as
    its main tool in decades) this is genuinely the operative policy rate.
    Daily.
  - GBP: Bank of England's own IADB statistical database (CSV). IUDBEDR is
    literally the Bank Rate -- the same figure the BoE's own press releases
    quote after every MPC decision. Daily.

USD and EUR are untouched here -- USD's fred_sync.py entry already points
at DFEDTARU (the literal FOMC target, fixed directly rather than routed
around), and EUR's ECBDFR is already the ECB's own literal deposit rate,
not a proxy.

Run standalone to see exactly what it would fetch:
    python central_bank_rates.py
"""
import concurrent.futures
import io
import json
import ssl
import urllib.request
from datetime import date, timedelta

import certifi
import openpyxl

import db

# RBNZ, SNB, BOJ, and BoE's endpoints 403 a bare urllib/curl User-Agent;
# RBA's endpoint does the opposite and 403s a real browser UA (confirmed by
# hand, both ways) -- so there isn't one UA that satisfies every source.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_UA_CURL = "curl/8.0"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())  # SNB's chain isn't in every OS's default trust store


def _fetch_cad():
    url = "https://www.bankofcanada.ca/valet/observations/V39079/json?recent=1"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    obs = data.get("observations", [])
    if not obs:
        return None, None
    return obs[-1]["d"], round(float(obs[-1]["V39079"]["v"]), 2)


def _fetch_aud():
    url = "https://www.rba.gov.au/statistics/tables/csv/f1.1-data.csv"
    req = urllib.request.Request(url, headers={"User-Agent": _UA_CURL})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8-sig", errors="replace")
    as_of, value = None, None
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or not parts[1].strip():
            continue
        try:
            value_candidate = float(parts[1])
        except ValueError:
            continue
        as_of, value = parts[0], value_candidate
    return as_of, round(value, 2) if value is not None else None


def _fetch_nzd():
    url = "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b/b2/hb2-daily-close.xlsx"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    ws = wb["Data"]
    as_of, value = None, None
    for row in ws.iter_rows(min_row=6, values_only=True):
        if row[0] is not None and row[1] is not None:
            as_of, value = row[0], row[1]
    return (as_of.strftime("%Y-%m-%d") if as_of else None), (round(float(value), 2) if value is not None else None)


def _fetch_chf():
    url = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
        text = resp.read().decode("utf-8-sig", errors="replace")
    as_of, value = None, None
    for line in text.splitlines():
        parts = [p.strip('"') for p in line.split(";")]
        if len(parts) < 3 or parts[1] != "LZ" or not parts[2]:
            continue
        as_of, value = parts[0], parts[2]
    return as_of, (round(float(value), 2) if value is not None else None)


def _fetch_jpy():
    start = (date.today() - timedelta(days=30)).strftime("%Y%m")
    url = f"https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db=FM01&code=STRDCLUCON&startDate={start}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    series = data.get("RESULTSET", [{}])[0].get("VALUES", {})
    dates, values = series.get("SURVEY_DATES", []), series.get("VALUES", [])
    for d, v in zip(reversed(dates), reversed(values)):
        if v is not None:
            return f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}", round(float(v), 2)
    return None, None


def _fetch_gbp():
    end = date.today()
    start = end - timedelta(days=45)
    url = (
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
        f"?csv.x=yes&SeriesCodes=IUDBEDR&UsingCodes=Y&CSVF=TN"
        f"&Datefrom={start.strftime('%d/%b/%Y')}&Dateto={end.strftime('%d/%b/%Y')}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    as_of, value = None, None
    for line in text.strip().splitlines():
        parts = line.split(",")
        if len(parts) != 2:
            continue
        try:
            value_candidate = float(parts[1])
        except ValueError:
            continue
        as_of, value = parts[0], value_candidate
    return as_of, round(value, 2) if value is not None else None


FETCHERS = {"CAD": _fetch_cad, "AUD": _fetch_aud, "NZD": _fetch_nzd, "CHF": _fetch_chf, "JPY": _fetch_jpy, "GBP": _fetch_gbp}


def _fetch_one(ccy):
    try:
        as_of, value = FETCHERS[ccy]()
    except Exception as e:
        return ccy, None, None, str(e)
    return ccy, as_of, value, None


def sync(currencies=None, dry_run=False):
    """Pull interest_rate for the covered currencies straight from each
    central bank and merge into the macro table -- everything else on the
    row is left untouched."""
    currencies = [c for c in (currencies or list(FETCHERS.keys())) if c in FETCHERS]
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}
    report = {}

    # Six independent central-bank requests -- pure network waits, so a
    # thread pool gets them all back in roughly the time of the single
    # slowest one instead of the sum of all six (same reasoning as
    # fred_sync.sync()).
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(currencies) or 1) as pool:
        results = list(pool.map(_fetch_one, currencies))

    for ccy, as_of, value, error in results:
        if error is not None:
            report[ccy] = {"metric": "interest_rate", "error": error}
            continue
        report[ccy] = {"metric": "interest_rate", "as_of": as_of, "value": value}
        if not dry_run and value is not None:
            existing = existing_by_ccy.get(ccy, {})
            merged = {key: existing.get(key) for key in db.MACRO_METRIC_KEYS}
            merged["bias"] = existing.get("bias")
            merged["notes"] = existing.get("notes")
            merged["interest_rate"] = value
            db.upsert_macro(ccy, merged)

    return report


if __name__ == "__main__":
    print(json.dumps(sync(dry_run=True), indent=2))
