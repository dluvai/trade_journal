"""
Server-side auto-sync: once a known calendar release time passes, this
background thread re-syncs that currency from FRED every minute for up to
45 minutes (or until the data actually changes) -- the same behavior
live_dashboard.html implements client-side, except this runs inside the
server process itself, so it still works even if nobody has the dashboard
open in a browser tab when a release happens.

Only events with a verified time_utc participate (see fred_calendar.py /
rate_calendar.py) -- BOJ decisions have no fixed announcement time, so they
fall back to the hourly full sync below (or the manual Sync button).

On top of the per-event polling, a full sync of every currency runs once an
hour regardless, as a safety net for anything the precise polling misses --
a release with no calendar entry, a missed window because the server was
asleep, etc.

Starts as a daemon thread at import time (module level, so it runs under
gunicorn too, not just `python server.py`). Safe with a single gunicorn
worker -- this app's Procfile doesn't set -w, so gunicorn defaults to one.
A second worker would run a second copy of this thread and double the FRED
calls, which is wasteful but not incorrect (a FRED sync is idempotent), so
no cross-process locking is implemented for that case.

Render free-tier caveat: a service that's spun down from inactivity isn't
running this thread (or anything else) until the next request wakes it --
this fixes the "nobody had the tab open" gap, not the "server was asleep"
gap. An always-on paid instance, or an external uptime pinger, would be
needed to close that one too.
"""
import threading
import time
from datetime import datetime, timezone

import calendar_view
import db
import fred_sync

CHECK_INTERVAL_SECONDS = 60
POLL_WINDOW_MINUTES = 45
FULL_SYNC_INTERVAL_SECONDS = 60 * 60

# event_key -> True once resolved (data changed, window expired, or a poll
# thread has already been started for it) -- in-memory only, so a server
# restart re-evaluates every event currently in the calendar window, which
# is exactly what we want (e.g. it's what catches an already-released
# metric right after a deploy).
_handled = {}


def _event_key(e):
    return f"{e['date']}|{e['currency']}|{e['label']}"


def _poll_after_release(event_key, currency):
    before = next((r for r in db.list_macro() if r["currency"] == currency), None)
    attempts = 0
    while attempts < POLL_WINDOW_MINUTES:
        attempts += 1
        try:
            fred_sync.sync(currencies=[currency])
        except Exception:
            pass  # transient FRED hiccup -- next minute retries
        after = next((r for r in db.list_macro() if r["currency"] == currency), None)
        if after != before:
            break
        time.sleep(CHECK_INTERVAL_SECONDS)
    _handled[event_key] = True


def _watch_loop():
    last_full_sync = 0.0
    while True:
        try:
            now = datetime.now(timezone.utc)
            for e in calendar_view.upcoming_events(days_ahead=1):
                if not e.get("time_utc"):
                    continue
                key = _event_key(e)
                if key in _handled:
                    continue
                elapsed_min = (now - datetime.fromisoformat(e["time_utc"])).total_seconds() / 60
                if 0 <= elapsed_min < POLL_WINDOW_MINUTES:
                    _handled[key] = "in_progress"  # claim it before spawning so we never double-poll one event
                    threading.Thread(target=_poll_after_release, args=(key, e["currency"]), daemon=True).start()
                elif elapsed_min >= POLL_WINDOW_MINUTES:
                    _handled.setdefault(key, True)

            now_ts = time.time()
            if now_ts - last_full_sync >= FULL_SYNC_INTERVAL_SECONDS:
                try:
                    fred_sync.sync()
                except Exception:
                    pass
                last_full_sync = now_ts
        except Exception:
            pass  # never let a bad calendar fetch or a bug here kill the thread
        time.sleep(CHECK_INTERVAL_SECONDS)


def start():
    threading.Thread(target=_watch_loop, daemon=True).start()
