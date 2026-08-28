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
    - JPY: BOJ's own Time-Series API. Series PRCG20_2200000000 under DB
      PR01 is "[Producer Price Index] All commodities" -- an index
      level, YoY computed the same way. ~1 month lag.
    - CHF: SNB's own data portal. Cube plproimpr, D0=P ("Index of
      producer prices" -- domestic only, not the blended import-price
      variant also in this cube), D1=VVP is already published by SNB as
      a YoY % figure -- no computation needed. ~1 month lag.

  retail_sales_yoy for GBP, CAD, and AUD -- same concept as USD's
  existing FRED-sourced figure (RSAFS), just from each country's own
  source since neither FRED nor OECD carry it for these three.
    - CAD: StatCan WDS. Vector 1446859483 is total retail sales (SA,
      table 20-10-0056), a dollar level -- YoY computed the same way as
      the PPI vector above. ~1 month lag.
    - GBP: ONS's timeseries API. Series J5EB is already ONS's own
      published YoY figure ("% change on same month a year ago") for
      volume-terms retail sales including fuel -- no computation needed,
      taken as-is. ~1 month lag.
    - AUD: ABS's own Retail Trade series was discontinued 31 July 2025;
      its official replacement is the Monthly Household Spending
      Indicator (dataflow HSI_M). Measure 9 ("Household spending - Index
      - Through the year percentage change"), category TOT, current
      prices, seasonally adjusted is already the YoY figure -- no
      computation needed. Getting a live response here needed an
      SDMX quirk: querying a single fixed dimension key 404s even with
      correct-looking codes, but requesting the "+"-joined measure set
      ABS's own default view uses (7+8+9) with lastNObservations
      returns real data that's then filtered down to measure 9 by its
      value id, not by an assumed key position -- ABS's own dimension
      ordering isn't guaranteed stable enough to hardcode positionally.
      ~1 month lag.

  current_account_pct_gdp -- a deliberately separate field from the
  existing current_account (USD-only, raw dollars), so no live currency
  conversion is needed to compare across currencies: expressing as a
  share of GDP is also the standard way economists compare external
  balances across differently-sized economies.
    - CAD only so far. StatCan's balance-of-payments table (36-10-0018)
      404/409s on every WDS metadata and vector-data endpoint tried --
      genuinely broken there, not a transient hiccup (retried across a
      long gap) -- so this instead downloads the table's full CSV export
      (getFullTableDownloadCSV, a different WDS endpoint that does work)
      and reads the "Balances, seasonally adjusted" x "Total current
      account" row directly. That's a quarterly flow in millions; GDP
      (vector 62305783) is StatCan's own seasonally-adjusted-at-annual-
      rate figure, so the quarterly current-account number is annualized
      (x4) before dividing, matching how this ratio is conventionally
      reported. ~1 quarter lag.
    - GBP: ONS's timeseries API. Series AA6H is already published by ONS
      as "current account balance as per cent of GDP" -- no computation
      needed, taken as-is. ~1 quarter lag.
    - AUD: ABS's BOP dataflow (current account, current prices, SA) and
      ANA_EXP dataflow (expenditure GDP, current prices, SA) divided
      directly -- unlike StatCan's SAAR convention, ABS doesn't annualize
      either series, so no x4 adjustment here. ~1 quarter lag.
    - CHF: SNB's own data portal, two cubes: bopoverq (D0=S0, "Current
      account, Net") and gdpap (D0=WMF/D1=BBIP, nominal GDP). Both plain
      quarterly levels like ABS's, divided directly -- checked the
      resulting ~7% ratio against Switzerland's well-documented large,
      persistent current-account surplus before trusting it, since a
      number this different in shape from CAD/GBP/AUD's small deficits
      was worth a sanity check. ~1 quarter lag.

Run standalone to see exactly what it would fetch:
    python national_stats_sync.py
"""
import concurrent.futures
import json
import re
import ssl
import urllib.request

import certifi

import db

_SNB_SSL_CTX = ssl.create_default_context(cafile=certifi.where())  # SNB's TLS chain isn't in every OS's default trust store

STATCAN_VECTOR_ID = 2062811  # Canada; Employment; Total; 15 years and over; SA
STATCAN_PPI_VECTOR_ID = 1230995983  # Canada; Total, Industrial product price index (IPPI)
STATCAN_RETAIL_VECTOR_ID = 1446859483  # Canada; Retail trade; Total retail sales; SA
STATCAN_GDP_VECTOR_ID = 62305783  # Canada; GDP at market prices; current $; SAAR
STATCAN_BOP_ZIP_URL = "https://www150.statcan.gc.ca/n1/tbl/csv/36100018-eng.zip"  # table's getCubeMetadata/vector endpoints 409 -- full-table CSV download is the only path that works
ABS_LF_URL = "https://data.api.abs.gov.au/rest/data/ABS,LF,1.0.0/M3.3.1599.20.AUS.M?dimensionAtObservation=AllDimensions&startPeriod={start}"
ABS_BOP_URL = "https://data.api.abs.gov.au/rest/data/ABS,BOP/1.100.20.Q?dimensionAtObservation=AllDimensions&lastNObservations=4"
ABS_GDP_URL = "https://data.api.abs.gov.au/rest/data/ABS,ANA_EXP/C.GPM.SSS.20.AUS.Q?dimensionAtObservation=AllDimensions&lastNObservations=4"
ABS_HSI_URL = "https://data.api.abs.gov.au/rest/data/ABS,HSI_M/7+8+9....AUS.M?dimensionAtObservation=AllDimensions&lastNObservations=6"
ABS_PPI_LATEST_RELEASE_URL = "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/producer-price-indexes-australia/latest-release"
ONS_MGRZ_URL = "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/mgrz/lms/data?format=json"
ONS_ECYX_URL = "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ecyx/mgdp/data?format=json"
ONS_GB7S_URL = "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/gb7s/ppi/data?format=json"
ONS_J5EB_URL = "https://www.ons.gov.uk/businessindustryandtrade/retailindustry/timeseries/j5eb/drsi/data?format=json"
ONS_AA6H_URL = "https://www.ons.gov.uk/economy/nationalaccounts/balanceofpayments/timeseries/aa6h/ukea/data?format=json"
BOJ_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"  # BOJ's API 403s a bare curl/urllib UA
SNB_UA = BOJ_UA  # SNB's API also 403s a bare curl/urllib UA (same discovery as central_bank_rates.py's CHF fetch)
SNB_PPI_URL = "https://data.snb.ch/api/cube/plproimpr/data/csv/en"
SNB_BOP_URL = "https://data.snb.ch/api/cube/bopoverq/data/csv/en"
SNB_GDP_URL = "https://data.snb.ch/api/cube/gdpap/data/csv/en"

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


def _boj_series_yoy(db_name, series_code, months_back=15):
    from datetime import date, timedelta
    start = (date.today() - timedelta(days=30 * months_back)).strftime("%Y%m")
    url = f"https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db={db_name}&code={series_code}&startDate={start}"
    req = urllib.request.Request(url, headers={"User-Agent": BOJ_UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    series = data["RESULTSET"][0]["VALUES"]
    rows = [(d, v) for d, v in zip(series["SURVEY_DATES"], series["VALUES"]) if v is not None]
    if len(rows) < 13:
        return None, None
    year_ago, latest = rows[-13], rows[-1]
    date_str = str(latest[0])
    as_of = f"{date_str[:4]}-{date_str[4:]}"
    return as_of, round((latest[1] / year_ago[1] - 1) * 100, 2)


def _fetch_jpy_ppi():
    return _boj_series_yoy("PR01", "PRCG20_2200000000")


def _snb_csv_rows(url, d0=None, d1=None):
    """Parse an SNB cube CSV export -- rows are "Date;D0[;D1];Value", with
    the D1 column only present for cubes that actually have a second
    dimension (checked per-cube via .../dimensions/en, not assumed)."""
    req = urllib.request.Request(url, headers={"User-Agent": SNB_UA})
    with urllib.request.urlopen(req, timeout=20, context=_SNB_SSL_CTX) as resp:
        text = resp.read().decode("utf-8-sig", errors="replace")
    rows = []
    for line in text.splitlines():
        parts = [p.strip('"') for p in line.split(";")]
        if d1 is not None:
            if len(parts) != 4 or parts[1] != d0 or parts[2] != d1 or not parts[3]:
                continue
            value = parts[3]
        else:
            if len(parts) != 3 or parts[1] != d0 or not parts[2]:
                continue
            value = parts[2]
        try:
            rows.append((parts[0], float(value)))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def _fetch_chf_ppi():
    rows = _snb_csv_rows(SNB_PPI_URL, d0="P", d1="VVP")
    if not rows:
        return None, None
    period, value = rows[-1]
    return period, round(value, 2)


def _fetch_chf_current_account_pct_gdp():
    ca_rows = _snb_csv_rows(SNB_BOP_URL, d0="S0")
    gdp_rows = _snb_csv_rows(SNB_GDP_URL, d0="WMF", d1="BBIP")
    if not ca_rows or not gdp_rows:
        return None, None
    gdp_by_period = dict(gdp_rows)
    period, ca_value = ca_rows[-1]
    gdp_value = gdp_by_period.get(period)
    if gdp_value is None:
        return None, None
    # Both series are already plain quarterly levels, so no annualizing is needed before dividing.
    return period, round(ca_value / gdp_value * 100, 2)


def _fetch_cad_ppi():
    return _statcan_vector_yoy(STATCAN_PPI_VECTOR_ID)


def _fetch_cad_retail():
    return _statcan_vector_yoy(STATCAN_RETAIL_VECTOR_ID)


def _fetch_cad_current_account_pct_gdp():
    import csv
    import io
    import zipfile
    req = urllib.request.Request(STATCAN_BOP_ZIP_URL, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        with z.open("36100018.csv") as f:
            text = f.read().decode("utf-8-sig")
    ca_period, ca_value = None, None
    for row in csv.DictReader(io.StringIO(text)):
        if row["Receipts, payments and balances"] == "Balances, seasonally adjusted" and row["Current account"] == "Total current account":
            ca_period, ca_value = row["REF_DATE"], float(row["VALUE"])
    if ca_period is None:
        return None, None

    gdp_url = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"
    payload = [{"vectorId": STATCAN_GDP_VECTOR_ID, "latestN": 4}]
    req2 = urllib.request.Request(
        gdp_url, data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": "curl/8.0", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req2, timeout=15) as resp:
        gdp_data = json.loads(resp.read().decode("utf-8"))
    gdp_value = next((p["value"] for p in gdp_data[0]["object"]["vectorDataPoint"] if p["refPer"][:7] == ca_period), None)
    if gdp_value is None:
        return None, None
    # Annualizes the quarterly flow (x4) before dividing, since gdp_value is already SAAR -- keeps the ratio on a comparable annual basis.
    return ca_period, round((ca_value * 4) / gdp_value * 100, 2)


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


def _fetch_gbp_current_account_pct_gdp():
    req = urllib.request.Request(ONS_AA6H_URL, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    quarters = data.get("quarters", [])
    if not quarters:
        return None, None
    latest = quarters[-1]
    return latest["date"], round(float(latest["value"]), 2)


def _fetch_aud_retail():
    req = urllib.request.Request(ABS_HSI_URL, headers={"User-Agent": "curl/8.0", "Accept": "application/vnd.sdmx.data+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        envelope = json.loads(resp.read().decode("utf-8"))
    data = envelope["data"]
    observations = data["dataSets"][0]["observations"]
    obs_dims = data["structures"][0]["dimensions"]["observation"]
    idx = {dim["id"]: i for i, dim in enumerate(obs_dims)}

    def _value_index(dim_id, value_id):
        return next(i for i, v in enumerate(obs_dims[idx[dim_id]]["values"]) if v["id"] == value_id)

    m_idx = _value_index("MEASURE", "9")  # Household spending - Index - Through the year percentage change
    cat_idx = _value_index("CATEGORY", "TOT")
    tsest_idx = _value_index("TSEST", "20")  # Seasonally Adjusted
    time_values = obs_dims[idx["TIME_PERIOD"]]["values"]

    rows = []
    for key, value_list in observations.items():
        parts = [int(p) for p in key.split(":")]
        if parts[idx["MEASURE"]] != m_idx or parts[idx["CATEGORY"]] != cat_idx or parts[idx["TSEST"]] != tsest_idx:
            continue
        if not value_list or value_list[0] is None:
            continue
        rows.append((time_values[parts[idx["TIME_PERIOD"]]]["id"], float(value_list[0])))
    if not rows:
        return None, None
    rows.sort(key=lambda r: r[0])
    period, value = rows[-1]
    return period, round(value, 2)


def _abs_series(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0", "Accept": "application/vnd.sdmx.data+json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
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
    return rows


def _fetch_aud_current_account_pct_gdp():
    ca_rows = _abs_series(ABS_BOP_URL)
    gdp_rows = _abs_series(ABS_GDP_URL)
    if not ca_rows or not gdp_rows:
        return None, None
    gdp_by_period = dict(gdp_rows)
    period, ca_value = ca_rows[-1]
    gdp_value = gdp_by_period.get(period)
    if gdp_value is None:
        return None, None
    # ABS series (unlike StatCan's CAD/SAAR) are already plain quarterly levels, so no annualizing is needed.
    return period, round(ca_value / gdp_value * 100, 2)


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
    "ppi_yoy": {"CAD": _fetch_cad_ppi, "AUD": _fetch_aud_ppi, "GBP": _fetch_gbp_ppi, "JPY": _fetch_jpy_ppi, "CHF": _fetch_chf_ppi},
    "retail_sales_yoy": {"CAD": _fetch_cad_retail, "GBP": _fetch_gbp_retail, "AUD": _fetch_aud_retail},
    "current_account_pct_gdp": {
        "CAD": _fetch_cad_current_account_pct_gdp,
        "GBP": _fetch_gbp_current_account_pct_gdp,
        "AUD": _fetch_aud_current_account_pct_gdp,
        "CHF": _fetch_chf_current_account_pct_gdp,
    },
}


def _fetch_one(job):
    metric, ccy = job
    try:
        as_of, value = FETCHERS[metric][ccy]()
    except Exception as e:
        return metric, ccy, None, None, str(e)
    return metric, ccy, as_of, value, None


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

    # Thread pool since each job is an independent network wait to a different institution -- same reasoning as fred_sync.sync().
    jobs = [(metric, ccy) for metric, fetchers in FETCHERS.items() for ccy in currencies if ccy in fetchers]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        results = list(pool.map(_fetch_one, jobs))

    for metric, ccy, as_of, value, error in results:
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
