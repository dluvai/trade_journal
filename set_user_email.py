"""Set (or change) an existing account's email address.

Needed for accounts created before email existed (e.g. felix's original
account) -- email-change isn't exposed in the /profile page on purpose, so
this is the escape hatch. Sets email_verified back to 0; the account owner
can re-verify via the normal forgot-password flow if they want a verified
email, or you can mark it verified directly with --verified.

    python set_user_email.py <username> <email> [--verified]
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    args = [a for a in sys.argv[1:] if a != "--verified"]
    verified = "--verified" in sys.argv
    if len(args) != 2:
        sys.exit("Usage: python set_user_email.py <username> <email> [--verified]")
    username, email = args
    if not EMAIL_RE.match(email):
        sys.exit(f"'{email}' doesn't look like a valid email address.")

    _load_dotenv()
    import db

    try:
        user = db.get_user_by_username(username)
        if not user:
            sys.exit(f"No user named '{username}'.")
        existing = db.get_user_by_email(email)
        if existing and existing["id"] != user["id"]:
            sys.exit(f"'{email}' is already used by another account ('{existing['username']}').")

        conn = db.get_conn()
        conn.execute(
            "UPDATE users SET email=?, email_verified=? WHERE id=?",
            (email, 1 if verified else 0, user["id"]),
        )
        print(f"Set email for '{username}' to {email} (verified={verified}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
