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

Run standalone to see exactly what it would fetch:
    python national_stats_sync.py
"""
import json
import urllib.request

import db

STATCAN_VECTOR_ID = 2062811  # Canada; Employment; Total; 15 years and over; SA
ABS_LF_URL = "https://data.api.abs.gov.au/rest/data/ABS,LF,1.0.0/M3.3.1599.20.AUS.M?dimensionAtObservation=AllDimensions&startPeriod={start}"
ONS_MGRZ_URL = "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/mgrz/lms/data?format=json"
ONS_ECYX_URL = "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ecyx/mgdp/data?format=json"

METRIC_NOTES = {
    ("GBP", "employment_change"): "3-month change vs. the prior 3 months (ONS's own headline convention), not a literal month-over-month diff -- the underlying LFS survey is too noisy for that.",
}


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


# metric -> {currency: fetch_fn}
FETCHERS = {
    "employment_change": {"CAD": _fetch_cad_employment, "AUD": _fetch_aud_employment, "GBP": _fetch_gbp_employment},
    "gdp_mom": {"GBP": _fetch_gbp_gdp_mom},
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
