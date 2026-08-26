"""
Optional automatic macro sync from FRED (Federal Reserve Economic Data).

Free, no API key needed -- uses the same public CSV export endpoint FRED's
own graph pages use (fred.stlouisfed.org/graph/fredgraph.csv), which is
openly served (robots.txt has no restrictions). Verified series freshness by
hand before wiring anything in -- see the comments below for what's covered
and what's deliberately left out.

Coverage, and not every metric for every currency:
  - PMI is never included -- it's licensed by S&P Global/ISM, nobody
    redistributes it for free.
  - Current account, core CPI, core PPI, core PCE, and retail sales are
    USD-only. Checked a same-family series for the other six currencies by
    hand -- current account exists but stopped updating in Oct 2024 for
    GBP/JPY/AUD/NZD/CHF (Oct 2022 for EUR) across every variant tried; core
    CPI is missing or multi-year stale everywhere it exists; retail sales
    has no working free mirror at all for any of the other six. Rather than
    show 18-24-month-old numbers as if current, these stay USD-only.
  - Trade balance covers USD, GBP, JPY, AUD, NZD, CHF (all fresh, monthly,
    USD-converted) -- EUR's mirror is stuck at Dec 2022, so EUR is the one
    currency left without it.
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
import concurrent.futures
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
        "cpi_mom":       ("CPIAUCSL", "mom"),               # same index, month-over-month
        "core_cpi_yoy":  ("CPILFESL", "yoy"),               # CPI less food & energy, monthly
        "core_ppi_yoy":  ("PPIFES", "yoy"),                 # PPI, Final Demand less food & energy, monthly
        "core_pce_yoy":  ("PCEPILFE", "yoy"),               # Core PCE price index -- the Fed's preferred gauge, monthly
        "core_pce_mom":  ("PCEPILFE", "mom"),               # same index, month-over-month
        "employment_change": ("PAYEMS", "mom_diff"),        # Nonfarm payrolls, monthly change (thousands of jobs)
        "retail_sales_yoy": ("RSAFS", "yoy"),               # Advance retail sales, monthly
        "trade_balance":    ("BOPGSTB", "level", 0.001),   # Trade balance, goods+services, monthly ($M -> $B)
        "current_account":  ("IEABC", "level", 0.001),     # Current account balance, quarterly ($M -> $B)
    },
    "EUR": {
        "interest_rate": ("ECBDFR", "level"),               # ECB deposit facility rate
        "gdp_yoy":       ("CLVMEURSCAB1GQEA19", "yoy"),     # Real GDP, Euro Area 19, quarterly
        "cpi_yoy":       ("CP0000EZ19M086NEST", "yoy"),     # HICP index, monthly
        "cpi_mom":       ("CP0000EZ19M086NEST", "mom"),     # same index, month-over-month
        "core_cpi_yoy":  ("TOTNRGFOODEA20MI15XM", "yoy"),   # HICP ex energy/food/alcohol/tobacco, Euro Area 20, monthly
    },
    "GBP": {
        "interest_rate": ("IR3TIB01GBM156N", "level"),      # 3-month interbank rate (BoE-rate proxy)
        "gdp_yoy":       ("NGDPRSAXDCGBQ", "yoy"),          # Real GDP, quarterly
        "unemployment":  ("LRHUTTTTGBM156S", "level"),
        "cpi_yoy":       ("FPCPITOTLZGGBR", "level"),       # World Bank annual inflation
        "trade_balance": ("XTNTVA01GBM667S", "level", 1e-9),  # $, exchange-rate converted -> $B
    },
    "JPY": {
        "interest_rate": ("IRSTCI01JPM156N", "level"),      # interbank call rate (BOJ-rate proxy)
        "gdp_yoy":       ("JPNRGDPEXP", "yoy"),             # Real GDP, quarterly
        "unemployment":  ("LRHUTTTTJPM156S", "level"),
        "cpi_yoy":       ("FPCPITOTLZGJPN", "level"),       # World Bank annual inflation
        "trade_balance": ("XTNTVA01JPM667S", "level", 1e-9),  # $, exchange-rate converted -> $B
    },
    "AUD": {
        "interest_rate": ("IRSTCI01AUM156N", "level"),      # interbank rate (RBA cash-rate proxy)
        "gdp_yoy":       ("NGDPRSAXDCAUQ", "yoy"),          # Real GDP, quarterly
        "unemployment":  ("LRHUTTTTAUM156S", "level"),
        "cpi_yoy":       ("FPCPITOTLZGAUS", "level"),       # World Bank annual inflation
        "trade_balance": ("XTNTVA01AUM667S", "level", 1e-9),  # $, exchange-rate converted -> $B
    },
    "NZD": {
        "interest_rate": ("IR3TIB01NZM156N", "level"),      # 3-month interbank rate (RBNZ OCR proxy)
        "gdp_yoy":       ("NZLGDPRQPSMEI", "qoq_to_yoy"),   # only available as QoQ -- compounded to an annual figure
        "cpi_yoy":       ("FPCPITOTLZGNZL", "level"),       # World Bank annual inflation
        "trade_balance": ("XTNTVA01NZM667S", "level", 1e-9),  # $, exchange-rate converted -> $B
        # unemployment: no fresh free FRED mirror found for NZ -- stays manual
    },
    "CHF": {
        "interest_rate": ("IR3TIB01CHM156N", "level"),      # 3-month interbank rate (SNB policy-rate proxy)
        "gdp_yoy":       ("CHEGDPRQPSMEI", "qoq_to_yoy"),   # only available as QoQ -- compounded to an annual figure
        "cpi_yoy":       ("FPCPITOTLZGCHE", "level"),       # World Bank annual inflation
        "trade_balance": ("XTNTVA01CHM667S", "level", 1e-9),  # $, exchange-rate converted -> $B
        # unemployment: no fresh free FRED mirror found for CH -- stays manual
    },
    "CAD": {
        "interest_rate": ("IR3TIB01CAM156N", "level"),      # 3-month interbank rate (BoC policy-rate proxy)
        "gdp_yoy":       ("NGDPRSAXDCCAQ", "yoy"),          # Real GDP, quarterly
        "unemployment":  ("LRHUTTTTCAM156S", "level"),
        "cpi_yoy":       ("FPCPITOTLZGCAN", "level"),       # World Bank annual inflation
        "trade_balance": ("XTNTVA01CAM667S", "level", 1e-9),  # $, exchange-rate converted -> $B
    },
}

PROXY_NOTES = {
    ("GBP", "interest_rate"): "3-month interbank rate used as a proxy for BoE policy stance, not the literal Bank Rate.",
    ("JPY", "interest_rate"): "Interbank call rate used as a proxy for BOJ policy stance, not the literal policy rate.",
    ("AUD", "interest_rate"): "Interbank rate used as a proxy for RBA cash-rate stance, not the literal cash rate.",
    ("NZD", "interest_rate"): "3-month interbank rate used as a proxy for RBNZ OCR stance, not the literal OCR.",
    ("CHF", "interest_rate"): "3-month interbank rate used as a proxy for SNB policy-rate stance, not the literal policy rate.",
    ("CAD", "interest_rate"): "3-month interbank rate used as a proxy for BoC policy-rate stance, not the literal policy rate.",
    ("GBP", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
    ("JPY", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
    ("AUD", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
    ("NZD", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
    ("CHF", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
    ("CAD", "cpi_yoy"): "World Bank annual inflation figure -- updates once a year, not monthly.",
    ("NZD", "gdp_yoy"): "No YoY series available -- compounded from the last 4 quarterly growth readings instead.",
    ("CHF", "gdp_yoy"): "No YoY series available -- compounded from the last 4 quarterly growth readings instead.",
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


def _mom(rows):
    if len(rows) < 2:
        return (rows[-1][0], None) if rows else (None, None)
    latest_date, latest_value = rows[-1]
    _, prior_value = rows[-2]
    if prior_value == 0:
        return latest_date, None
    return latest_date, round((latest_value / prior_value - 1) * 100, 2)


def _mom_diff(rows):
    # For a level series like nonfarm payrolls, the conventional "change"
    # figure is the raw difference (e.g. "+150K jobs"), not a percentage --
    # a % change on a number already in the hundred-millions is meaningless
    # to read at a glance.
    if len(rows) < 2:
        return (rows[-1][0], None) if rows else (None, None)
    latest_date, latest_value = rows[-1]
    _, prior_value = rows[-2]
    return latest_date, round(latest_value - prior_value, 2)


def _qoq_compounded_to_yoy(rows):
    # NZ and CH's GDP mirrors on FRED are already quarter-over-quarter growth
    # rates (confirmed by hand: values oscillate roughly -2..+4, including
    # negatives -- an index/level series never does that), not an index like
    # GDPC1/GDPRSAXDC*, so there's nothing to take a ratio of. Compounding
    # the last 4 quarters gives a genuine annual growth figure that's
    # comparable to every other currency's YoY GDP number instead of mixing
    # QoQ and YoY across the currency switcher.
    if len(rows) < 4:
        return (rows[-1][0], None) if rows else (None, None)
    latest_date = rows[-1][0]
    last4 = [v for _, v in rows[-4:]]
    compounded = 1.0
    for q in last4:
        compounded *= (1 + q / 100)
    return latest_date, round((compounded - 1) * 100, 2)


def fetch_metric(series_id, mode):
    rows = _fetch_series(series_id)
    if not rows:
        return None, None
    if mode == "level":
        d, v = rows[-1]
        return d, round(v, 2)
    if mode == "qoq_to_yoy":
        return _qoq_compounded_to_yoy(rows)
    if mode == "mom":
        return _mom(rows)
    if mode == "mom_diff":
        return _mom_diff(rows)
    return _yoy(rows)


def _fetch_one(job):
    ccy, metric, spec = job
    series_id, mode = spec[0], spec[1]
    scale = spec[2] if len(spec) > 2 else 1
    try:
        as_of, value = fetch_metric(series_id, mode)
    except Exception as e:
        return ccy, metric, series_id, None, None, str(e)
    if value is not None and scale != 1:
        value = round(value * scale, 2)
    return ccy, metric, series_id, as_of, value, None


def sync(currencies=None, dry_run=False):
    """Pull the covered metrics for each currency and merge them into the
    macro table -- manually-entered fields not covered here (PMI, bias,
    notes, and anything for uncovered currencies) are left untouched."""
    currencies = currencies or list(SERIES.keys())
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}
    report = {ccy: {} for ccy in currencies}
    fetched_by_ccy = {ccy: {} for ccy in currencies}

    jobs = [(ccy, metric, spec) for ccy in currencies for metric, spec in SERIES.get(ccy, {}).items()]

    # ~30-40 independent FRED requests across every currency -- these are
    # pure network waits, not CPU work, so a thread pool genuinely
    # parallelizes them despite the GIL (urllib releases it while blocked on
    # the socket). Measured by hand: capping this at max_workers=10 still
    # took ~21s, because once 10 are in flight the 11th has to wait for a
    # slot to free rather than starting immediately -- a couple of slow
    # FRED responses in the first batch delay every batch behind them. One
    # worker per job removes that queueing entirely: total time drops to
    # ~6s, bounded by the single slowest request instead of by batches of
    # stragglers compounding.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        results = list(pool.map(_fetch_one, jobs))

    for ccy, metric, series_id, as_of, value, error in results:
        if error is not None:
            report[ccy][metric] = {"series_id": series_id, "error": error}
            continue
        report[ccy][metric] = {
            "series_id": series_id, "as_of": str(as_of) if as_of else None,
            "value": value, "note": PROXY_NOTES.get((ccy, metric)),
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
    import json
    print(json.dumps(sync(dry_run=True), indent=2))
