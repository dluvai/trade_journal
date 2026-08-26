"""
Next policy-rate-decision date per currency.

Unlike fred_calendar.py, there's no free API for this -- central bank
meeting calendars aren't a FRED release, and every non-Fed bank would need
its own integration. Each currency's dates below were pulled by hand,
directly from that bank's own published calendar, on 2026-08-26:

  USD -- https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
  EUR -- https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html
  GBP -- https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates
  JPY -- https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm
  AUD -- https://www.rba.gov.au/schedules-events/board-meeting-schedules.html

NZD and CHF are left out on purpose, not guessed:
  - RBNZ's site (rbnz.govt.nz) returns 403 to every automated request tried
    (plain fetch, browser user-agent, an authenticated fetch tool) -- can't
    verify their calendar without a browser session.
  - SNB only publishes assessment dates once they've already happened; their
    site doesn't expose the remaining 2026 dates (September/December) in a
    fetchable form. Their cadence is well known (mid-March/June/Sept/Dec)
    but a guessed date is worse than none -- it would look authoritative
    while being confidently wrong.

This list WILL go stale -- it only covers 2026 decisions and needs a manual
refresh (repeat the lookups above) once these run out or a new year starts.
"""
from datetime import date

DECISION_DATES = {
    "USD": ["2026-09-16", "2026-10-28", "2026-12-09"],
    "EUR": ["2026-09-10", "2026-10-29", "2026-12-17"],
    "GBP": ["2026-09-17", "2026-11-05", "2026-12-17"],
    "JPY": ["2026-09-18", "2026-10-30", "2026-12-18"],
    "AUD": ["2026-09-29", "2026-11-03", "2026-12-08"],
}


def next_decisions():
    """{currency: 'YYYY-MM-DD' or None} -- the earliest listed date that
    hasn't happened yet, per currency. NZD/CHF always come back None."""
    today = date.today().isoformat()
    result = {}
    for ccy in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF"):
        dates = DECISION_DATES.get(ccy, [])
        upcoming = [d for d in dates if d >= today]
        result[ccy] = min(upcoming) if upcoming else None
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(next_decisions(), indent=2))
