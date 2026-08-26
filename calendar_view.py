"""
Merges fred_calendar.py (USD statistical releases) and rate_calendar.py
(policy-rate decisions) into one sorted, upcoming-events list -- the
building block for a single glanceable "what's coming up" view instead of
a "next release" line buried inside each individual metric card.

Deliberately just a merge-and-sort, not a new data source: each calendar
module still owns fetching/verifying its own events, this only combines
what they already produce.
"""
import fred_calendar
import rate_calendar


def upcoming_events(days_ahead=14):
    events = fred_calendar.upcoming_events(days_ahead) + rate_calendar.upcoming_events(days_ahead)
    events.sort(key=lambda e: (e["date"], e["currency"]))
    return events


if __name__ == "__main__":
    import json
    print(json.dumps(upcoming_events(), indent=2))
