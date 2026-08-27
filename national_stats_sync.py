"""
Optional automatic sync for macro metrics that only a specific country's
own statistics office publishes in a form usable here -- neither FRED nor
OECD carry a usable series for any of these, so each is pulled directly
from source instead.

Covers two metric families:

  employment_change (net jobs added/lost), for the non-USD currencies
  whose employment report is an actual FX market mover. CAD's "Net Change
  in Employment" and AUD's "Employment Change" are both headline monthly
  releases that reliably move their currency; GBP's isn't quite the same
  shape (see below) but is still real and automatable. EUR/JPY/NZD/CHF
  don't have a comparable single monthly market-moving employment-change
  figure (Eurozone's is a lagged quarterly aggregate; NZ's HLFS is
  quarterly; JPY and CHF employment prints don't move FX the way the
  other three do) -- these stay manual, same as core CPI/PCE/PPI already
  are for everyone but USD.
    - CAD: Statistics Canada's Web Data Service (free, no key). Vector
      v2062811 is total employment (persons, SA, 15+) from table
      14-10-0287 -- the level whose month-over-month change is StatCan's
      own "net change in employment" headline. ~1 month lag.
    - AUD: ABS's SDMX Data API (free, no key). Dataflow LF, measure M3
      (Employed persons, SA, national) is the level behind the ABS Labour
      Force headline "employment change". ~1 month lag.
    - GBP: ONS's timeseries API (free, no key). Series MGRZ is the LFS
      employment level (16+, SA) -- but ONS's own headline "employment
      change" is a rolling 3-months-vs-previous-3-months comparison, not
      a literal month-over-month diff (the underlying LFS survey is too
      noisy month to month for that). Computed that way here, not as a
      plain MoM diff, so the number actually matches what's reported as
      "UK employment change" everywhere else. ~3 month lag as a result --
      the 3-month window needs the newest month to have already landed.

  gdp_mom (month-over-month GDP growth), GBP only -- the UK is the only
  major economy that publishes an official monthly GDP estimate at all
  (every other major economy is quarterly-only, already covered by
  gdp_yoy). Series ECYX under ONS's MGDP dataset is "Gross Value Added -
  Monthly (period on period growth)", the exact figure ONS's own monthly
  GDP bulletins headline as UK growth for the month. ~2 month lag.

  ppi_yoy (headline producer price index, YoY) -- deliberately a separate
  metric from core_ppi_yoy, not the same field: core_ppi_yoy is a US-
  specific ex-food-and-energy construct (FRED's PPIFES), while what's
  fetched here for GBP/CAD/AUD is each country's plain all-items PPI --
  putting a different concept into the "core" field would be the same
  class of mistake as an earlier bug this app already had and fixed
  (USD's interest_rate briefly pointed at the wrong FRED series).
    - CAD: StatCan WDS. Vector 1230995983 is the Industrial Product Price
      Index total (table 18-10-0265), an index level -- YoY computed here
      the same way fred_sync.py does for any other index series. ~1 month
      lag.
    - AUD: ABS's PPI dataflow, oddly, only carries the SDMX API's live
      data down to Manufacturing-division granularity, not the true
      economy-wide headline (checked and confirmed: MEASURE=3,
      TYPE=OUTPUT,FREQ=Q lists just 4 index codes, all sub-national-
      total). The real headline -- "Final Demand" -- isn't exposed there
      at all; it only exists in ABS's downloadable release table (catalog
      6427.0, table 1), so this fetches that XLSX directly from the
      "latest-release" page (URL path changes every quarter, so the page
      is scraped for the current link each sync) and reads the column
      matching "Final ; Total (Source)" by its own header text -- not a
      hardcoded Series ID, since ABS revises those. ~1 quarter lag.
    - GBP: ONS's timeseries API. Series GB7S is Output PPI (domestic,
      manufactured products) as an index level -- YoY computed the same
      way as CAD's. ~1 month lag.

  retail_sales_yoy for GBP and CAD -- same concept as USD's existing
  FRED-sourced figure (RSAFS), just from each country's own source since
  neither FRED nor OECD carry it for these two.
    - CAD: StatCan WDS. Vector 1446859483 is total retail sales (SA,
      table 20-10-0056), a dollar level -- YoY computed the same way as
      the PPI vector above. ~1 month lag.
    - GBP: ONS's timeseries API. Series J5EB is already ONS's own
      published YoY figure ("% change on same month a year ago") for
      volume-terms retail sales including fuel -- no computation needed,
      taken as-is. ~1 month lag.

Run standalone to see exactly what it would fetch:
    python national_stats_sync.py
"""
import json
import re
import urllib.request

import db

STATCAN_VECTOR_ID = 2062811  # Canada; Employment; Total; 15 years and over; SA
STATCAN_PPI_VECTOR_ID = 1230995983  # Canada; Total, Industrial product price index (IPPI)
STATCAN_RETAIL_VECTOR_ID = 1446859483  # Canada; Retail trade; Total retail sales; SA
ABS_LF_URL = "https://data.api.abs.gov.au/rest/data/ABS,LF,1.0.0/M3.3.1599.20.AUS.M?dimensionAtObservation=AllDimensions&startPeriod={start}"
ABS_PPI_LATEST_RELEASE_URL = "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/producer-price-indexes-australia/latest-release"
ONS_MGRZ_URL = "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/mgrz/lms/data?format=json"
ONS_ECYX_URL = "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ecyx/mgdp/data?format=json"
ONS_GB7S_URL = "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/gb7s/ppi/data?format=json"
ONS_J5EB_URL = "https://www.ons.gov.uk/businessindustryandtrade/retailindustry/timeseries/j5eb/drsi/data?format=json"

METRIC_NOTES = {
    ("GBP", "employment_change"): "3-month change vs. the prior 3 months (ONS's own headline convention), not a literal month-over-month diff -- the underlying LFS survey is too noisy for that.",
    ("AUD", "ppi_yoy"): "ABS 'Final Demand' PPI -- pulled from their release table directly since it isn't exposed on their SDMX API.",
}


def _statcan_vector_yoy(vector_id):
    url = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"
    payload = [{"vectorId": vector_id, "latestN": 13}]
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": "curl/8.0", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    points = data[0]["object"]["vectorDataPoint"]
    if len(points) < 13:
        return None, None
    year_ago, latest = points[0], points[-1]
    return latest["refPer"][:7], round((latest["value"] / year_ago["value"] - 1) * 100, 2)


def _ons_index_yoy(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    months = data.get("months", [])
    if len(months) < 13:
        return None, None
    year_ago, latest = months[-13], months[-1]
    return latest["date"], round((float(latest["value"]) / float(year_ago["value"]) - 1) * 100, 2)


def _fetch_cad_employment():
    url = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"
    payload = [{"vectorId": STATCAN_VECTOR_ID, "latestN": 2}]
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": "curl/8.0", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    points = data[0]["object"]["vectorDataPoint"]
    if len(points) < 2:
        return None, None
    prev_v, latest = points[-2]["value"], points[-1]
    return latest["refPer"][:7], round(latest["value"] - prev_v, 1)


def _fetch_aud_employment():
    from datetime import date, timedelta
    start = (date.today() - timedelta(days=210)).strftime("%Y-%m")
    req = urllib.request.Request(
        ABS_LF_URL.format(start=start),
        headers={"User-Agent": "curl/8.0", "Accept": "application/vnd.sdmx.data+json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        envelope = json.loads(resp.read().decode("utf-8"))
    data = envelope["data"]
    observations = data["dataSets"][0]["observations"]
    obs_dims = data["structures"][0]["dimensions"]["observation"]
    time_idx = next(i for i, d in enumerate(obs_dims) if d["id"] == "TIME_PERIOD")
    time_values = obs_dims[time_idx]["values"]
    rows = []
    for key, value_list in observations.items():
        if not value_list or value_list[0] is None:
            continue
        idx = int(key.split(":")[time_idx])
        rows.append((time_values[idx]["id"], float(value_list[0])))
    rows.sort(key=lambda r: r[0])
    if len(rows) < 2:
        return None, None
    (_, prev_v), (period, latest_v) = rows[-2], rows[-1]
    return period, round(latest_v - prev_v, 1)


def _fetch_gbp_employment():
    req = urllib.request.Request(ONS_MGRZ_URL, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    months = data.get("months", [])
    if len(months) < 6:
        return None, None
    last3 = [float(m["value"]) for m in months[-3:]]
    prev3 = [float(m["value"]) for m in months[-6:-3]]
    as_of = months[-1]["date"]  # e.g. "2026 MAY"
    change = (sum(last3) / 3) - (sum(prev3) / 3)
    return as_of, round(change, 1)


def _fetch_gbp_gdp_mom():
    req = urllib.request.Request(ONS_ECYX_URL, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    months = data.get("months", [])
    if not months:
        return None, None
    latest = months[-1]
    return latest["date"], round(float(latest["value"]), 2)


def _fetch_cad_ppi():
    return _statcan_vector_yoy(STATCAN_PPI_VECTOR_ID)


def _fetch_cad_retail():
    return _statcan_vector_yoy(STATCAN_RETAIL_VECTOR_ID)


def _fetch_gbp_ppi():
    return _ons_index_yoy(ONS_GB7S_URL)


def _fetch_gbp_retail():
    req = urllib.request.Request(ONS_J5EB_URL, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    months = data.get("months", [])
    if not months:
        return None, None
    latest = months[-1]
    return latest["date"], round(float(latest["value"]), 2)


def _fetch_aud_ppi():
    req = urllib.request.Request(
        ABS_PPI_LATEST_RELEASE_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    match = re.search(r'href="([^"]+/642701\.xlsx)"', html)
    if not match:
        return None, None
    xlsx_url = "https://www.abs.gov.au" + match.group(1)
    req2 = urllib.request.Request(
        xlsx_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"},
    )
    with urllib.request.urlopen(req2, timeout=20) as resp:
        raw = resp.read()
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    ws = wb["Data1"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    col = next((i for i, h in enumerate(header) if h and "corresponding quarter of previous year" in h.lower() and "final" in h.lower() and "total (source)" in h.lower()), None)
    if col is None:
        return None, None
    as_of, value = None, None
    for row in rows[10:]:
        if row[0] is not None and row[col] is not None:
            as_of, value = row[0], row[col]
    return (as_of.strftime("%Y-%m-%d") if as_of else None), (round(float(value), 2) if value is not None else None)


# metric -> {currency: fetch_fn}
FETCHERS = {
    "employment_change": {"CAD": _fetch_cad_employment, "AUD": _fetch_aud_employment, "GBP": _fetch_gbp_employment},
    "gdp_mom": {"GBP": _fetch_gbp_gdp_mom},
    "ppi_yoy": {"CAD": _fetch_cad_ppi, "AUD": _fetch_aud_ppi, "GBP": _fetch_gbp_ppi},
    "retail_sales_yoy": {"CAD": _fetch_cad_retail, "GBP": _fetch_gbp_retail},
}


def _fetch_one(metric, ccy):
    try:
        as_of, value = FETCHERS[metric][ccy]()
    except Exception as e:
        return None, None, str(e)
    return as_of, value, None


def sync(currencies=None, dry_run=False):
    """Pull every covered (currency, metric) job and merge into the macro
    table, one write per currency -- everything else on the row (PMI,
    bias, notes, and metrics this module doesn't cover) is left
    untouched."""
    covered_ccys = {ccy for fetchers in FETCHERS.values() for ccy in fetchers}
    currencies = [c for c in (currencies or sorted(covered_ccys)) if c in covered_ccys]
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}
    report = {ccy: {} for ccy in currencies}
    fetched_by_ccy = {ccy: {} for ccy in currencies}

    for metric, fetchers in FETCHERS.items():
        for ccy in currencies:
            if ccy not in fetchers:
                continue
            as_of, value, error = _fetch_one(metric, ccy)
            if error is not None:
                report[ccy][metric] = {"error": error}
                continue
            report[ccy][metric] = {"as_of": as_of, "value": value, "note": METRIC_NOTES.get((ccy, metric))}
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
