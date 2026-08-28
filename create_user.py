"""Create a new login for this dashboard.

Run this yourself, locally -- it writes directly to the shared Turso
database (same one the deployed app uses), so it works whether or not the
deployed app happens to be up.

    python create_user.py <username>

You'll be prompted for a password (not taken as a command-line argument, so
it never ends up in your shell history).
"""
import getpass
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

HERE = Path(__file__).parent


def _load_dotenv():
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    import os
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main():
    if len(sys.argv) != 2:
        print("Usage: python create_user.py <username>")
        raise SystemExit(1)
    username = sys.argv[1].strip()

    _load_dotenv()
    import db

    # Explicit close() needed on every exit path, or the script hangs since libsql_client's background thread isn't a daemon.
    try:
        if db.get_user_by_username(username):
            print(f"A user named '{username}' already exists.")
            raise SystemExit(1)

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords didn't match.")
            raise SystemExit(1)
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            raise SystemExit(1)

        user_id = db.create_user(username, generate_password_hash(password))
        print(f"Created user '{username}' (id={user_id}). They can now log in with this username and password.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
