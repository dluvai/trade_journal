"""Pure helpers for verification codes and password-reset codes.

No Flask or db.py imports -- just code generation, hashing, and expiry
math, so this is trivially testable and reusable across the signup and
forgot-password flows.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

CODE_TTL_MINUTES = 15
MAX_VERIFICATION_ATTEMPTS = 5
MAX_RESET_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 30


def generate_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def code_matches(code, code_hash):
    if not code or not code_hash:
        return False
    return secrets.compare_digest(hash_code(code), code_hash)


def expiry_timestamp(minutes=CODE_TTL_MINUTES):
    return (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def is_expired(expires_at):
    if not expires_at:
        return True
    return datetime.now() > datetime.fromisoformat(expires_at)


def seconds_until_resend_allowed(expires_at):
    # Issue time is derived from expires_at - CODE_TTL_MINUTES rather than stored in its own column.
    if not expires_at:
        return 0
    issued_at = datetime.fromisoformat(expires_at) - timedelta(minutes=CODE_TTL_MINUTES)
    elapsed = (datetime.now() - issued_at).total_seconds()
    return max(0, RESEND_COOLDOWN_SECONDS - elapsed)
