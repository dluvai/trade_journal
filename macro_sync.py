"""
Unified macro-data sync: dispatches each (currency, metric) job to whichever
of fred_sync.py, oecd_sync.py, employment_sync.py, or
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
import central_bank_rates
import employment_sync
import fred_sync
import oecd_sync
import db

# (currency, metric) -> "oecd" for the pairs verified as a concrete
# upgrade; everything else falls through to fred_sync (FRED's own
# per-currency coverage, itself already partial -- see fred_sync.py).
OECD_ROUTED = {
    ("GBP", "cpi_yoy"), ("GBP", "unemployment"),
    ("CAD", "cpi_yoy"), ("CAD", "unemployment"),
    ("AUD", "cpi_yoy"), ("AUD", "unemployment"),
    ("EUR", "unemployment"),
    ("NZD", "cpi_yoy"), ("NZD", "unemployment"),
    ("CHF", "unemployment"),
}

# employment_change for the three currencies whose employment report is an
# actual FX market mover -- see employment_sync.py's docstring for why
# these three and not the others. USD's employment_change is unaffected,
# still comes from fred_sync (nonfarm payrolls).
EMPLOYMENT_ROUTED = {("CAD", "employment_change"), ("AUD", "employment_change"), ("GBP", "employment_change")}

# interest_rate for every currency whose own central bank publishes it
# directly -- see central_bank_rates.py's docstring for the exact source
# per currency. USD and EUR are untouched: fred_sync's entries for both are
# already the literal rate (DFEDTARU, ECBDFR), not a proxy, so there's
# nothing for this router to improve on there.
CENTRAL_BANK_ROUTED = {(ccy, "interest_rate") for ccy in central_bank_rates.FETCHERS}


def sync(currencies=None, dry_run=False):
    """Pull the covered metrics for each currency from whichever source is
    routed for that (currency, metric) pair and merge them into the macro
    table in one write per currency -- manually-entered fields not covered
    by either source (PMI, bias, notes, and anything for uncovered
    currencies) are left untouched."""
    currencies = currencies or list(db.MAJOR_CURRENCIES)
    existing_by_ccy = {r["currency"]: r for r in db.list_macro()}

    # Both providers are queried in dry-run mode regardless of routing --
    # fred_sync/oecd_sync each already skip any currency missing from their
    # own SERIES/OECD_SERIES dict, so there's no wasted work in asking both
    # and then picking, per (currency, metric), which report to keep.
    fred_report = fred_sync.sync(currencies=currencies, dry_run=True)
    oecd_report = oecd_sync.sync(currencies=currencies, dry_run=True)
    employment_report = employment_sync.sync(currencies=currencies, dry_run=True)
    rates_report = central_bank_rates.sync(currencies=currencies, dry_run=True)

    report = {ccy: {} for ccy in currencies}
    fetched_by_ccy = {ccy: {} for ccy in currencies}

    for ccy in currencies:
        for metric in fred_sync.SERIES.get(ccy, {}):
            if (ccy, metric) in OECD_ROUTED or (ccy, metric) in CENTRAL_BANK_ROUTED:
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

        if (ccy, "employment_change") in EMPLOYMENT_ROUTED:
            entry = employment_report.get(ccy)
            if entry:
                entry = dict(entry, source="national_stats")
                report[ccy]["employment_change"] = entry
                if entry.get("error") is None and entry.get("value") is not None:
                    fetched_by_ccy[ccy]["employment_change"] = entry["value"]

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
