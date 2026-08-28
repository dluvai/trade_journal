"""Grant (or revoke) admin access for an existing account.

Needed since there's no UI path to create the first admin -- bootstrap
Felix's own account with:

    python set_admin.py felix
    python set_admin.py <username> --revoke
"""
import sys
from pathlib import Path

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
    args = [a for a in sys.argv[1:] if a != "--revoke"]
    revoke = "--revoke" in sys.argv
    if len(args) != 1:
        sys.exit("Usage: python set_admin.py <username> [--revoke]")
    username = args[0]

    _load_dotenv()
    import db

    try:
        user = db.get_user_by_username(username)
        if not user:
            sys.exit(f"No user named '{username}'.")
        db.set_admin(user["id"], is_admin=not revoke)
        print(f"{'Revoked' if revoke else 'Granted'} admin for '{username}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
