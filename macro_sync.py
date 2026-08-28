"""
Unified macro-data sync: dispatches each (currency, metric) job to whichever
of fred_sync.py, oecd_sync.py, national_stats_sync.py, or
central_bank_rates.py is the better source, then does one merged
db.upsert_macro() write per currency -- never independent passes racing
each other.

Why one dispatch table instead of calling fred_sync.sync() then
oecd_sync.sync() back to back: auto_sync.py's per-event poller diffs a
before/after db.list_macro() snapshot around a single sync call, and its
hourly full sync can overlap with a per-event poll. Two independent
writers racing doubles the surface for a lost update; routing everything
through one sync() call here that does exactly one upsert_macro() per
currency avoids that entirely. fred_sync.py and oecd_sync.py both stay
independently runnable for standalone debugging -- this module only
decides who to ask.

The provider table below reflects what was verified fresh, by hand, at
the time each switch was made (see the OECD-integration plan for the
detailed freshness comparison). It is intentionally conservative: only
currency/metric pairs where OECD was a clear, concrete improvement over
FRED's existing behavior were moved. Everything not listed here keeps
using fred_sync -- see fred_sync.py's own docstring for what it covers
and why.

Run standalone to see exactly what it would fetch, and from where:
    python macro_sync.py
"""
import concurrent.futures

import central_bank_rates
import national_stats_sync
import fred_sync
import oecd_sync
import db

# Only pairs verified as a concrete upgrade route to OECD; everything else falls through to FRED's own (partial) coverage.
OECD_ROUTED = {
    ("GBP", "cpi_yoy"), ("GBP", "unemployment"),
    ("CAD", "cpi_yoy"), ("CAD", "unemployment"),
    ("AUD", "cpi_yoy"), ("AUD", "unemployment"),
    ("EUR", "unemployment"),
    ("NZD", "cpi_yoy"), ("NZD", "unemployment"),
    ("CHF", "unemployment"),
}

# Mirrors national_stats_sync's coverage; only adds GBP's gdp_mom, leaving USD employment and everyone's gdp_yoy untouched.
NATIONAL_STATS_ROUTED = {
    (ccy, metric) for metric, fetchers in national_stats_sync.FETCHERS.items() for ccy in fetchers
}

# Routes interest_rate to each currency's own central bank, except USD/EUR where fred_sync already has the literal (non-proxy) rate.
CENTRAL_BANK_ROUTED = {(ccy, "interest_rate") for ccy in central_bank_rates.FETCHERS}


def sync(currencies=None, dry_run=False):
    """Pull the covered metrics for each currency from whichever source is
    routed for that (currency, metric) pair and merge them into the macro
    table in one write per currency -- manually-entered fields not covered
    by either source (PMI, bias, notes, and anything for uncovered
    currencies) are left untouched."""
    currencies = currencies or list(db.MAJOR_CURRENCIES)
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}

    # All four providers now run concurrently (each already parallel internally) since running them sequentially wasted the internal parallelism.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fred_future = pool.submit(fred_sync.sync, currencies=currencies, dry_run=True)
        oecd_future = pool.submit(oecd_sync.sync, currencies=currencies, dry_run=True)
        national_stats_future = pool.submit(national_stats_sync.sync, currencies=currencies, dry_run=True)
        rates_future = pool.submit(central_bank_rates.sync, currencies=currencies, dry_run=True)
        fred_report = fred_future.result()
        oecd_report = oecd_future.result()
        national_stats_report = national_stats_future.result()
        rates_report = rates_future.result()

    report = {ccy: {} for ccy in currencies}
    fetched_by_ccy = {ccy: {} for ccy in currencies}

    for ccy in currencies:
        for metric in fred_sync.SERIES.get(ccy, {}):
            if (ccy, metric) in OECD_ROUTED or (ccy, metric) in CENTRAL_BANK_ROUTED or (ccy, metric) in NATIONAL_STATS_ROUTED:
                continue
            entry = fred_report.get(ccy, {}).get(metric)
            if not entry:
                continue
            entry = dict(entry, source="fred")
            report[ccy][metric] = entry
            if entry.get("error") is None and entry.get("value") is not None:
                fetched_by_ccy[ccy][metric] = entry["value"]

        for metric in oecd_sync.OECD_SERIES.get(ccy, {}):
            if (ccy, metric) not in OECD_ROUTED:
                continue
            entry = oecd_report.get(ccy, {}).get(metric)
            if not entry:
                continue
            entry = dict(entry, source="oecd")
            report[ccy][metric] = entry
            if entry.get("error") is None and entry.get("value") is not None:
                fetched_by_ccy[ccy][metric] = entry["value"]

        for metric in national_stats_sync.FETCHERS:
            if (ccy, metric) not in NATIONAL_STATS_ROUTED:
                continue
            entry = national_stats_report.get(ccy, {}).get(metric)
            if not entry:
                continue
            entry = dict(entry, source="national_stats")
            report[ccy][metric] = entry
            if entry.get("error") is None and entry.get("value") is not None:
                fetched_by_ccy[ccy][metric] = entry["value"]

        if (ccy, "interest_rate") in CENTRAL_BANK_ROUTED:
            entry = rates_report.get(ccy)
            if entry:
                entry = dict(entry, source="central_bank")
                report[ccy]["interest_rate"] = entry
                if entry.get("error") is None and entry.get("value") is not None:
                    fetched_by_ccy[ccy]["interest_rate"] = entry["value"]

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
