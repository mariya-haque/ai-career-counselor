"""Passwords and login tokens.

Hashing is stdlib scrypt rather than bcrypt or argon2, so the app keeps its short
dependency list. scrypt is memory-hard, which buys far more resistance per
millisecond of login latency than raising a pbkdf2 iteration count: these
parameters cost ~150ms and 16MB per attempt, and the memory is what makes
attacking a stolen table on GPUs expensive.
"""

import hashlib
import hmac
import re
import secrets

# n must be a power of two. Memory used is roughly 128 * n * r bytes.
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32, "maxmem": 64 * 1024 * 1024}

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

MIN_PASSWORD = 8


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), **SCRYPT).hex()
    return digest, salt


def verify_password(password, digest, salt):
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, digest)


def new_token():
    return secrets.token_urlsafe(32)


def clean_email(email):
    return (email or "").strip().lower()


def signup_problem(name, email, password, invite, expected_invite):
    """The first thing wrong with a signup, phrased for the person reading it."""
    if expected_invite and not hmac.compare_digest((invite or "").strip(), expected_invite):
        return "That invite code is not right."
    if not (name or "").strip():
        return "Please enter your name."
    if not EMAIL.match(clean_email(email)):
        return "That does not look like an email address."
    if len(password or "") < MIN_PASSWORD:
        return f"Use a password of at least {MIN_PASSWORD} characters."
    return None
