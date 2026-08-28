"""
Automatic + on-demand backups of the Turso database.

Backups are a logical JSON dump of every table -- the old approach used
SQLite's Online Backup API to copy the local trades.db file, which only
works against a local file. There's no local file anymore (everything lives
in Turso), so this dumps each table's rows as JSON instead.

These still land on whatever disk the app is running on (BACKUP_DIR, next
to this script) -- on Render's free tier that's ephemeral and vanishes on
redeploy, same as before. That's an acceptable secondary safety net now
that the real data lives in Turso, not the source of truth.

server.py calls backup_if_needed() once at import time (module level, not
inside `if __name__ == "__main__"`) so it fires under gunicorn in production
too, not just when run directly with `python server.py`.
"""
import json
from datetime import date, datetime
from pathlib import Path

import db

BACKUP_DIR = Path(__file__).parent / "backups"
KEEP_LAST = 30
FILENAME_PREFIX = "trades_"
TABLES = ["users", "trades", "strategies", "settings", "macro"]


def _has_backup_today():
    # Checks by filename prefix, not an exact name, since backup_now() timestamps files so same-day calls don't clobber each other.
    if not BACKUP_DIR.exists():
        return False
    today_prefix = f"{FILENAME_PREFIX}{date.today().isoformat()}"
    return any(p.name.startswith(today_prefix) for p in BACKUP_DIR.glob(f"{today_prefix}*.json"))


def backup_now():
    """Take a fresh backup right now, timestamped to the second (so calling
    this manually more than once a day doesn't overwrite the earlier one)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest_path = BACKUP_DIR / f"{FILENAME_PREFIX}{stamp}.json"

    conn = db.get_conn()
    dump = {table: [row.asdict() for row in conn.execute(f"SELECT * FROM {table}").rows] for table in TABLES}
    dest_path.write_text(json.dumps(dump, indent=2), encoding="utf-8")

    _prune_old_backups()
    return dest_path


def backup_if_needed():
    """Back up once per calendar day -- called at import time, so the first
    request of the day (whether that's you opening the dashboard, or
    gunicorn starting up on Render) triggers it, with no scheduler needed."""
    if _has_backup_today():
        return None
    return backup_now()


def _prune_old_backups():
    backups = sorted(BACKUP_DIR.glob(f"{FILENAME_PREFIX}*.json"))
    for old in backups[:-KEEP_LAST]:
        old.unlink()


def list_backups():
    if not BACKUP_DIR.exists():
        return []
    return sorted(
        (
            {"filename": p.name, "size_bytes": p.stat().st_size, "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in BACKUP_DIR.glob(f"{FILENAME_PREFIX}*.json")
        ),
        key=lambda b: b["filename"],
        reverse=True,
    )


if __name__ == "__main__":
    path = backup_now()
    print(f"Backed up to {path}")
    print(f"\nBackups on disk ({BACKUP_DIR}):")
    for b in list_backups():
        print(f"  {b['filename']}  {b['size_bytes']:,} bytes  {b['modified']}")
    db.close()
