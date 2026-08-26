"""
Automatic + on-demand backups of trades.db.

Backups live in a "backups" folder *next to* the real database (db.DB_PATH's
own directory), not a hardcoded path -- on Render that directory is the
persistent disk mount, so backups survive redeploys the same way the real
data does. Point this at a local trades.db and backups land right next to
it instead.

Uses SQLite's own Online Backup API (sqlite3.Connection.backup), not a raw
file copy. A plain copy can grab the database mid-write and produce a
corrupt snapshot; the backup API guarantees a consistent copy even while
something else has the database open.

server.py calls backup_if_needed() once at import time (module level, not
inside `if __name__ == "__main__"`) so it fires under gunicorn in production
too, not just when run directly with `python server.py`.
"""
import sqlite3
from datetime import date, datetime

import db

BACKUP_DIR = db.DB_PATH.parent / "backups"
KEEP_LAST = 30
FILENAME_PREFIX = "trades_"


def _has_backup_today():
    # backup_now() names files with a full date_time stamp (so calling it
    # twice in a day doesn't clobber the first copy) -- so "already backed
    # up today" has to check by prefix, not for one exact filename.
    if not BACKUP_DIR.exists():
        return False
    today_prefix = f"{FILENAME_PREFIX}{date.today().isoformat()}"
    return any(p.name.startswith(today_prefix) for p in BACKUP_DIR.glob(f"{today_prefix}*.db"))


def backup_now():
    """Take a fresh, consistent backup right now, timestamped to the second
    (so calling this manually more than once a day doesn't overwrite the
    earlier one)."""
    if not db.DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest_path = BACKUP_DIR / f"{FILENAME_PREFIX}{stamp}.db"

    source = sqlite3.connect(db.DB_PATH)
    dest = sqlite3.connect(dest_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    _prune_old_backups()
    return dest_path


def backup_if_needed():
    """Back up once per calendar day -- called at import time, so the first
    request of the day (whether that's you opening the dashboard, or
    gunicorn starting up on Render) triggers it, with no scheduler needed."""
    if not db.DB_PATH.exists():
        return None
    if _has_backup_today():
        return None
    return backup_now()


def _prune_old_backups():
    backups = sorted(BACKUP_DIR.glob(f"{FILENAME_PREFIX}*.db"))
    for old in backups[:-KEEP_LAST]:
        old.unlink()


def list_backups():
    if not BACKUP_DIR.exists():
        return []
    return sorted(
        (
            {"filename": p.name, "size_bytes": p.stat().st_size, "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in BACKUP_DIR.glob(f"{FILENAME_PREFIX}*.db")
        ),
        key=lambda b: b["filename"],
        reverse=True,
    )


if __name__ == "__main__":
    path = backup_now()
    print(f"Backed up to {path}" if path else "No database found to back up.")
    print(f"\nBackups on disk ({BACKUP_DIR}):")
    for b in list_backups():
        print(f"  {b['filename']}  {b['size_bytes']:,} bytes  {b['modified']}")
