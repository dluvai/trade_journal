"""
US federal debt-sustainability snapshot -- current debt/GDP, a one-year-ahead
projection, and the four "what would need to change to stabilize debt/GDP"
figures (growth, deficit, rate, inflation).

This is a standard, simplified textbook debt-dynamics identity (the same one
the IMF and central banks use in their own debt-sustainability write-ups),
not a forecast and not anyone's proprietary model:

    change in debt/GDP  ~=  new borrowing (% of GDP)  +  debt/GDP * (r - g) / 100

  where r = the rate used to finance the debt (here: the Fed funds rate, a
  proxy for financing conditions -- not literally the average rate on the
  existing stock of debt, which isn't published as one clean free series)
  and g = nominal GDP growth (real growth + inflation).

Each "stabilizer" answers one question: holding every other input at its
current value, what would this one input need to be for debt/GDP to stop
rising? That's a sensitivity read, not a prediction of what will happen.

USD only -- getting a comparable free public-debt series for the other six
currencies here would mean sourcing each country's own treasury data one at
a time; not attempted.
"""
import time

import fred_sync

_cache = {}

DEBT_SERIES = "GFDEBTN"             # Total public debt, $ millions, quarterly
GDP_SERIES = "GDP"                  # Nominal GDP, $ billions, quarterly
DEFICIT_PCT_SERIES = "FYFSGDA188S"  # Federal surplus(+)/deficit(-) as % of GDP, annual
RATE_SERIES = "FEDFUNDS"            # reused as the "r" (financing rate) proxy


def _latest(series_id):
    rows = fred_sync._fetch_series(series_id)
    return rows[-1] if rows else (None, None)


def snapshot():
    # Cached for hours since these series update at most quarterly, avoiding a refetch on every macro-tab visit.
    hit = _cache.get("snapshot")
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    result = _compute_snapshot()
    _cache["snapshot"] = (time.time(), result)
    return result


def _compute_snapshot():
    debt_date, debt = _latest(DEBT_SERIES)
    gdp_date, gdp = _latest(GDP_SERIES)
    deficit_date, deficit_pct_raw = _latest(DEFICIT_PCT_SERIES)
    rate_date, rate = _latest(RATE_SERIES)

    if None in (debt, gdp, deficit_pct_raw, rate):
        return {"available": False}

    debt_gdp = debt / 1000 / gdp * 100  # debt is $M, GDP is $B -- match units
    borrowing_pct = -deficit_pct_raw    # flip sign: positive = adding to debt

    gdp_rows = fred_sync._fetch_series(GDP_SERIES)
    _, nominal_growth = fred_sync._yoy(gdp_rows)
    _, cpi_yoy = fred_sync.fetch_metric("CPIAUCSL", "yoy")
    real_growth = (nominal_growth - cpi_yoy) if (nominal_growth is not None and cpi_yoy is not None) else None

    result = {
        "available": True,
        "as_of": str(max(d for d in (debt_date, gdp_date, deficit_date, rate_date) if d)),
        "debt_gdp_pct": round(debt_gdp, 2),
        "borrowing_pct_of_gdp": round(borrowing_pct, 2),
        "rate_pct": rate,
        "nominal_growth_pct": round(nominal_growth, 2) if nominal_growth is not None else None,
        "real_growth_pct": round(real_growth, 2) if real_growth is not None else None,
        "projected_next_year_pct": None,
        "stabilizers": {},
    }

    if nominal_growth is None:
        return result

    result["projected_next_year_pct"] = round(
        debt_gdp + borrowing_pct + debt_gdp * (rate - nominal_growth) / 100, 2
    )
    # Each stabilizer solves the debt-dynamics identity for one variable, holding the others fixed.
    stabilizers = {
        "growth_needed": round(rate + borrowing_pct * 100 / debt_gdp, 2),
        "deficit_allowance": round(debt_gdp * (nominal_growth - rate) / 100, 2),
        "rate_needed": round(nominal_growth - borrowing_pct * 100 / debt_gdp, 2),
    }
    if real_growth is not None:
        stabilizers["inflation_needed"] = round(stabilizers["growth_needed"] - real_growth, 2)
    result["stabilizers"] = stabilizers
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), indent=2))
