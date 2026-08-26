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
  CAD -- https://www.bankofcanada.ca/press/upcoming-events/

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

Announcement times, also hand-verified against each bank's own site/press
materials (not guessed): every regularly-scheduled decision from these six
banks goes out at the same time of day, every time.
  USD -- FOMC statement, 2:00 PM America/New_York
  EUR -- ECB decision press release, 14:15 Europe/Berlin (moved from 13:45 in
         a 2024 format change; press conference follows at 14:45)
  GBP -- BoE publishes the decision with the MPC minutes at 12:00 Europe/London
  AUD -- RBA cash rate announcement, 2:30 PM Australia/Sydney
  CAD -- BoC rate announcement, 9:45 AM America/Toronto
JPY is deliberately left without a time: the BOJ has no fixed announcement
time (it floats roughly 11:45-13:00 JST on decision day) -- a guessed time
would look authoritative while being wrong.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DECISION_DATES = {
    "USD": ["2026-09-16", "2026-10-28", "2026-12-09"],
    "EUR": ["2026-09-10", "2026-10-29", "2026-12-17"],
    "GBP": ["2026-09-17", "2026-11-05", "2026-12-17"],
    "JPY": ["2026-09-18", "2026-10-30", "2026-12-18"],
    "AUD": ["2026-09-29", "2026-11-03", "2026-12-08"],
    "CAD": ["2026-09-02", "2026-10-28", "2026-12-09"],
}

DECISION_TIME = {
    "USD": (14, 0, "America/New_York"),
    "EUR": (14, 15, "Europe/Berlin"),
    "GBP": (12, 0, "Europe/London"),
    "AUD": (14, 30, "Australia/Sydney"),
    "CAD": (9, 45, "America/Toronto"),
}


def _time_utc_for(ccy, d_obj):
    spec = DECISION_TIME.get(ccy)
    if not spec:
        return None
    hour, minute, tz = spec
    local = datetime(d_obj.year, d_obj.month, d_obj.day, hour, minute, tzinfo=ZoneInfo(tz))
    return local.astimezone(ZoneInfo("UTC")).isoformat()


def next_decisions():
    """{currency: 'YYYY-MM-DD' or None} -- the earliest listed date that
    hasn't happened yet, per currency. NZD/CHF always come back None."""
    today = date.today().isoformat()
    result = {}
    for ccy in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD"):
        dates = DECISION_DATES.get(ccy, [])
        upcoming = [d for d in dates if d >= today]
        result[ccy] = min(upcoming) if upcoming else None
    return result


def upcoming_events(days_ahead=14):
    """[{date, currency, label, time_utc}] for every rate decision due in the
    next `days_ahead` days, across every currency -- same shape as
    fred_calendar.upcoming_events() so the two can be merged into one list.
    time_utc is None for currencies without a verified fixed announcement
    time (JPY)."""
    today = date.today()
    cutoff = (today + timedelta(days=days_ahead)).isoformat()
    today_str = today.isoformat()
    events = []
    for ccy, dates in DECISION_DATES.items():
        for d in dates:
            if today_str <= d <= cutoff:
                d_obj = datetime.strptime(d, "%Y-%m-%d").date()
                events.append({"date": d, "currency": ccy, "label": "Rate decision", "time_utc": _time_utc_for(ccy, d_obj)})
    return events


if __name__ == "__main__":
    import json
    print(json.dumps(next_decisions(), indent=2))
