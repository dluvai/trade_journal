"""
Optional automatic sync for employment_change (net jobs added/lost) on the
non-USD currencies whose employment report is an actual FX market mover --
each pulled from that country's own statistics office, since none of FRED
or OECD carry a usable monthly employment *level* series for these three
(OECD's LFS dataflows only go down to quarterly employed-population, which
would badly lag the real monthly release the market trades off).

Currencies covered, and why these three specifically: CAD's "Net Change in
Employment" and AUD's "Employment Change" are both headline monthly
releases that reliably move their currency; GBP's isn't quite the same
shape (see below) but is still real and automatable. EUR/JPY/NZD/CHF don't
have a comparable single monthly market-moving employment-change figure
(Eurozone's is a lagged quarterly aggregate; NZ's HLFS is quarterly; JPY
and CHF employment prints don't move FX the way the other three do) --
these stay manual, same as core CPI/PCE/PPI already are for everyone but
USD.

  - CAD: Statistics Canada's Web Data Service (free, no key). Vector
    v2062811 is total employment (persons, SA, 15+) from table 14-10-0287 --
    the level whose month-over-month change is StatCan's own "net change
    in employment" headline. ~1 month lag.
  - AUD: ABS's SDMX Data API (free, no key). Dataflow LF, measure M3
    (Employed persons, SA, national) is the level behind the ABS Labour
    Force headline "employment change". ~1 month lag.
  - GBP: ONS's timeseries API (free, no key). Series MGRZ is the LFS
    employment level (16+, SA) -- but ONS's own headline "employment
    change" is a rolling 3-months-vs-previous-3-months comparison, not a
    literal month-over-month diff (the underlying LFS survey is too noisy
    month to month for that). Computed that way here, not as a plain MoM
    diff, so the number actually matches what's reported as "UK
    employment change" everywhere else. ~3 month lag as a result -- the
    3-month window needs the newest month to have already landed.

Run standalone to see exactly what it would fetch:
    python employment_sync.py
"""
import json
import urllib.request

import db

STATCAN_VECTOR_ID = 2062811  # Canada; Employment; Total; 15 years and over; SA
ABS_LF_URL = "https://data.api.abs.gov.au/rest/data/ABS,LF,1.0.0/M3.3.1599.20.AUS.M?dimensionAtObservation=AllDimensions&startPeriod={start}"
ONS_MGRZ_URL = "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/mgrz/lms/data?format=json"

EMPLOYMENT_NOTES = {
    ("GBP", "employment_change"): "3-month change vs. the prior 3 months (ONS's own headline convention), not a literal month-over-month diff -- the underlying LFS survey is too noisy for that.",
}


def _fetch_cad():
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


def _fetch_aud():
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


def _fetch_gbp():
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


FETCHERS = {"CAD": _fetch_cad, "AUD": _fetch_aud, "GBP": _fetch_gbp}


def _fetch_one(ccy):
    try:
        as_of, value = FETCHERS[ccy]()
    except Exception as e:
        return ccy, None, None, str(e)
    return ccy, as_of, value, None


def sync(currencies=None, dry_run=False):
    """Pull employment_change for the covered currencies and merge into the
    macro table -- everything else on the row is left untouched."""
    currencies = [c for c in (currencies or list(FETCHERS.keys())) if c in FETCHERS]
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}
    report = {}

    for ccy in currencies:
        _, as_of, value, error = _fetch_one(ccy)
        if error is not None:
            report[ccy] = {"metric": "employment_change", "error": error}
            continue
        report[ccy] = {
            "metric": "employment_change", "as_of": as_of, "value": value,
            "note": EMPLOYMENT_NOTES.get((ccy, "employment_change")),
        }
        if not dry_run and value is not None:
            existing = existing_by_ccy.get(ccy, {})
            merged = {key: existing.get(key) for key in db.MACRO_METRIC_KEYS}
            merged["bias"] = existing.get("bias")
            merged["notes"] = existing.get("notes")
            merged["employment_change"] = value
            db.upsert_macro(ccy, merged)

    return report


if __name__ == "__main__":
    print(json.dumps(sync(dry_run=True), indent=2))
